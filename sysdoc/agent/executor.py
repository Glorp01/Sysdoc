"""Run PowerShell and cmd scripts with streamed output, timeouts, and UAC elevation."""
from __future__ import annotations

import codecs
import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from base64 import b64encode
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

import psutil

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ERROR_CANCELLED = 1223
HEAD_CHARS = 60_000
TAIL_LINES = 1_500

OutputCallback = Callable[[str], None]

_PS_PRELUDE = (
    "$ProgressPreference = 'SilentlyContinue'\n"
    "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}\n"
    "$OutputEncoding = [System.Text.Encoding]::UTF8\n"
)


@dataclass
class CommandResult:
    exit_code: int | None
    output: str
    duration: float
    timed_out: bool = False
    cancelled: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not (self.timed_out or self.cancelled or self.error)

    def describe(self) -> str:
        if self.error:
            return self.error
        if self.cancelled:
            return "Cancelled."
        if self.timed_out:
            return f"Timed out after {self.duration:.0f}s."
        return f"Exit code {self.exit_code}."


def is_admin() -> bool:
    if sys.platform != "win32":
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _system_program(*parts: str, fallback: str) -> str:
    # A full path stops a same-named program in the working directory from being run instead.
    path = Path(os.environ.get("SystemRoot", r"C:\Windows"), "System32", *parts)
    return str(path) if path.is_file() else fallback


def powershell_exe() -> str:
    return _system_program("WindowsPowerShell", "v1.0", "powershell.exe", fallback="powershell.exe")


def clip(text: str, limit: int = 12_000) -> str:
    """Shorten long output for the model, keeping the start and the (usually more useful) end."""
    if len(text) <= limit:
        return text
    head = limit // 3
    tail = limit - head
    omitted = len(text) - limit
    return f"{text[:head]}\n... [{omitted} characters omitted] ...\n{text[-tail:]}"


def _powershell_source(script: str, strict: bool) -> str:
    preference = "Stop" if strict else "Continue"
    return (
        f"{_PS_PRELUDE}$ErrorActionPreference = '{preference}'\n"
        "$global:LASTEXITCODE = 0\n"
        f"try {{\n{script}\n}}\n"
        "catch {\n"
        "    Write-Output ('ERROR: ' + $_.Exception.Message)\n"
        "    if ($_.InvocationInfo) { Write-Output $_.InvocationInfo.PositionMessage }\n"
        "    exit 1\n"
        "}\n"
        "if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
    )


class _OutputCollector:
    """Decodes streamed bytes into lines, keeping a bounded head and tail of the output."""

    def __init__(self, on_output: OutputCallback | None) -> None:
        self._on_output = on_output
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._partial = ""
        self._pending: Queue[str] = Queue()
        self._head: list[str] = []
        self._head_chars = 0
        self._tail: deque[str] = deque(maxlen=TAIL_LINES)
        self._dropped = 0
        self._last: str | None = None

    def feed(self, data: bytes, final: bool = False) -> None:
        # Some Windows tools (sfc, DISM) write UTF-16; dropping NULs recovers their ASCII text.
        text = self._decoder.decode(data, final).replace("\x00", "").replace("\ufeff", "")
        text = (self._partial + text).replace("\r\n", "\n").replace("\r", "\n")
        *lines, self._partial = text.split("\n")
        if final and self._partial:
            lines.append(self._partial)
            self._partial = ""
        for line in lines:
            self._pending.put(line.rstrip())

    def pump(self, stream) -> None:
        while chunk := stream.read1(8192):
            self.feed(chunk)
        self.feed(b"", final=True)

    def deliver(self) -> None:
        while True:
            try:
                line = self._pending.get_nowait()
            except Empty:
                return
            if line == self._last or (not line and self._last in (None, "")):
                continue
            self._last = line
            if self._head_chars < HEAD_CHARS:
                self._head.append(line)
                self._head_chars += len(line) + 1
            else:
                if len(self._tail) == self._tail.maxlen:
                    self._dropped += 1
                self._tail.append(line)
            if self._on_output:
                self._on_output(line)

    def text(self) -> str:
        lines = list(self._head)
        if self._dropped:
            lines.append(f"... [{self._dropped} lines omitted] ...")
        lines.extend(self._tail)
        return "\n".join(lines).strip("\n")


def _kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.Error:
        pass


def _run_process(args: list[str], timeout: float | None, on_output: OutputCallback | None) -> CommandResult:
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
        )
    except OSError as exc:
        return CommandResult(None, "", 0.0, error=f"Could not start {Path(args[0]).name}: {exc}")

    collector = _OutputCollector(on_output)
    reader = threading.Thread(target=collector.pump, args=(process.stdout,), daemon=True)
    reader.start()
    timed_out = cancelled = False
    try:
        while True:
            try:
                process.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                collector.deliver()
                if timeout and time.monotonic() - started > timeout:
                    timed_out = True
                    _kill_tree(process.pid)
                    break
    except KeyboardInterrupt:
        cancelled = True
        _kill_tree(process.pid)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    reader.join(timeout=5)
    collector.deliver()
    exit_code = None if timed_out or cancelled else process.returncode
    return CommandResult(exit_code, collector.text(), time.monotonic() - started, timed_out, cancelled)


def run_script(
    script: str,
    shell: str = "powershell",
    *,
    timeout: float | None = None,
    on_output: OutputCallback | None = None,
    elevated: bool = False,
    strict: bool = False,
) -> CommandResult:
    """Run a script and stream its output.

    ``strict`` stops PowerShell at the first error, which fix steps use so a failure
    is reported instead of silently skipped. ``elevated`` shows a UAC prompt when
    Sysdoc isn't already running as administrator.
    """
    if elevated and sys.platform == "win32" and not is_admin():
        return _run_elevated(script, timeout, on_output, strict)
    workdir = Path(tempfile.mkdtemp(prefix="sysdoc-"))
    try:
        if shell == "cmd":
            path = workdir / "step.cmd"
            body = script.replace("\r\n", "\n").replace("\n", "\r\n")
            path.write_text(f"@echo off\r\nchcp 65001 >nul\r\n{body}\r\n", encoding="utf-8")
            args = [_system_program("cmd.exe", fallback="cmd.exe"), "/d", "/c", str(path)]
        else:
            path = workdir / "step.ps1"
            path.write_text(_powershell_source(script, strict), encoding="utf-8-sig")
            args = [powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)]
        return _run_process(args, timeout, on_output)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def ps_quote(value: str) -> str:
    """Quote text as a single-quoted PowerShell string; PowerShell also treats typographic quotes as quotes."""
    return "'" + "".join(char * 2 if char in "'‘’‚‛" else char for char in value) + "'"


def _run_elevated(script: str, timeout: float | None, on_output: OutputCallback | None, strict: bool) -> CommandResult:
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    workdir = Path(tempfile.mkdtemp(prefix="sysdoc-admin-"))
    log = workdir / "output.log"
    started = time.monotonic()
    try:
        step = workdir / "step.ps1"
        payload = _powershell_source(script, strict).encode("utf-8")
        step.write_bytes(payload)
        log.touch()
        encoded = _encode(_elevation_wrapper(step, log, payload))
        info = ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(ShellExecuteInfo)
        info.fMask = 0x00000040 | 0x00000400  # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
        info.lpVerb = "runas"
        info.lpFile = powershell_exe()
        info.lpParameters = f"-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand {encoded}"
        info.nShow = 0
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            code = ctypes.get_last_error()
            if code == ERROR_CANCELLED:
                return CommandResult(None, "", 0.0, error="Administrator permission was declined.")
            return CommandResult(None, "", 0.0, error=f"Could not start an administrator PowerShell (Windows error {code}).")

        collector = _OutputCollector(on_output)
        position = 0
        timed_out = cancelled = False

        def read_log() -> None:
            nonlocal position
            try:
                with open(log, "rb") as handle:
                    handle.seek(position)
                    data = handle.read()
            except OSError:
                return
            position += len(data)
            if data:
                collector.feed(data)
            collector.deliver()

        try:
            while kernel32.WaitForSingleObject(info.hProcess, 200) != 0:
                read_log()
                if timeout and time.monotonic() - started > timeout:
                    timed_out = True
                    break
        except KeyboardInterrupt:
            cancelled = True
        stopped = True
        if timed_out or cancelled:
            stopped = bool(kernel32.TerminateProcess(info.hProcess, 1))
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
        kernel32.CloseHandle(info.hProcess)
        read_log()
        collector.feed(b"", final=True)
        collector.deliver()
        error = None if stopped else "The administrator step could not be stopped and may still be running."
        code = None if timed_out or cancelled else int(exit_code.value)
        return CommandResult(code, collector.text(), time.monotonic() - started, timed_out, cancelled, error)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _encode(script: str) -> str:
    """Encode a script for powershell -EncodedCommand."""
    return b64encode(script.encode("utf-16-le")).decode("ascii")


def _elevation_wrapper(step: Path, log: Path, payload: bytes) -> str:
    """PowerShell that runs the approved script only if its file is unchanged, logging all output for Sysdoc to show."""
    return (
        f"{_PS_PRELUDE}$log = {ps_quote(str(log))}\n"
        f"$bytes = [IO.File]::ReadAllBytes({ps_quote(str(step))})\n"
        "$hash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes)).Replace('-', '')\n"
        f"if ($hash -ne '{hashlib.sha256(payload).hexdigest().upper()}') {{\n"
        "    [IO.File]::AppendAllText($log, \"Refusing to run: the script changed after it was approved.`n\")\n"
        "    exit 97\n"
        "}\n"
        "try { $block = [ScriptBlock]::Create([Text.Encoding]::UTF8.GetString($bytes)) }\n"
        "catch { [IO.File]::AppendAllText($log, \"ERROR: $($_.Exception.Message)`n\"); exit 1 }\n"
        "& $block *>&1 | Out-String -Stream -Width 240 | ForEach-Object { [IO.File]::AppendAllText($log, $_ + \"`n\") }\n"
        "exit 0\n"
    )
