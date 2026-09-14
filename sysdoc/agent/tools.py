"""Tools the AI uses to investigate the PC, plus the question and fix-plan tools.

Investigation tools only read. The session decides whether each call needs the
user's permission and refuses commands that would change the PC.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import platform
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import psutil

from sysdoc.agent.executor import clip, is_admin, ps_quote, run_script
from sysdoc.agent.plan import PLAN_PARAMETERS
from sysdoc.agent.safety import is_sensitive_path
from sysdoc.core.models import Severity
from sysdoc.core.orchestrator import Orchestrator
from sysdoc.providers.base import ToolSpec
from sysdoc.scanners.network import NetworkScanner
from sysdoc.scanners.storage import StorageScanner

OUTPUT_LIMIT = 12_000
READ_CHUNK = 256_000
ASK_USER = "ask_user"
PROPOSE_PLAN = "propose_plan"
RUN_COMMAND = "run_command"


@dataclass
class ToolOutcome:
    content: str
    summary: str
    is_error: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    title: str
    run: Callable[[dict[str, Any]], ToolOutcome]
    detail: Callable[[dict[str, Any]], str]


def _failure(message: str) -> ToolOutcome:
    return ToolOutcome(message, message, is_error=True)


def _text_arg(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    return value.strip() if isinstance(value, str) else ""


def _int_arg(args: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        number = int(args.get(key, default))
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _bool_arg(args: dict[str, Any], key: str) -> bool:
    value = args.get(key, False)
    return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes")


def expand_path(raw: str) -> Path:
    text = raw.strip().strip("\"'")
    text = re.sub(r"\$env:(\w+)", lambda match: os.environ.get(match.group(1), match.group(0)), text, flags=re.IGNORECASE)
    return Path(os.path.expanduser(os.path.expandvars(text)))


def _size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


# --- Registry helpers --------------------------------------------------------

def _registry(hive: str, path: str, *names: str) -> dict[str, Any] | None:
    """Read values from a registry key; None if the key doesn't exist."""
    if sys.platform != "win32":
        return None
    import winreg
    root = winreg.HKEY_LOCAL_MACHINE if hive == "HKLM" else winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(root, path) as key:
            values = {}
            for name in names:
                try:
                    values[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    pass
            return values
    except OSError:
        return None


def _subkeys(hive: str, path: str) -> list[str]:
    if sys.platform != "win32":
        return []
    import winreg
    root = winreg.HKEY_LOCAL_MACHINE if hive == "HKLM" else winreg.HKEY_CURRENT_USER
    names = []
    try:
        with winreg.OpenKey(root, path) as key:
            while True:
                try:
                    names.append(winreg.EnumKey(key, len(names)))
                except OSError:
                    return names
    except OSError:
        return names


# --- system_overview ---------------------------------------------------------

def windows_version() -> str:
    info = _registry("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                     "ProductName", "DisplayVersion", "CurrentBuildNumber", "UBR")
    if not info:
        return platform.platform()
    build = str(info.get("CurrentBuildNumber", ""))
    name = str(info.get("ProductName", "Windows"))
    if build.isdigit() and int(build) >= 22000:
        name = name.replace("Windows 10", "Windows 11")  # The registry still says 10 on Windows 11.
    version = f" {info['DisplayVersion']}" if info.get("DisplayVersion") else ""
    patch = f".{info['UBR']}" if "UBR" in info else ""
    return f"{name}{version} (build {build}{patch})"


def _gpus() -> list[str]:
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    gpus = []
    for subkey in _subkeys("HKLM", base):
        if subkey.isdigit():
            values = _registry("HKLM", f"{base}\\{subkey}", "DriverDesc", "DriverVersion", "DriverDate") or {}
            if values.get("DriverDesc"):
                gpus.append(f"{values['DriverDesc']} · driver {values.get('DriverVersion', '?')} "
                            f"({values.get('DriverDate', 'unknown date')})")
    return list(dict.fromkeys(gpus))


# Two Get-Process snapshots take about a second; psutil's per-process calls take several on a busy PC.
_PROCESS_SAMPLE = (
    "$first = @{}\n"
    "Get-Process | ForEach-Object { $first[$_.Id] = $_.CPU }\n"
    "$started = Get-Date\n"
    "Start-Sleep -Milliseconds 700\n"
    "$seconds = ((Get-Date) - $started).TotalSeconds * [Environment]::ProcessorCount\n"
    "$culture = [Globalization.CultureInfo]::InvariantCulture\n"
    "Get-Process | Where-Object { $_.Id -ne 0 } | ForEach-Object {\n"
    "    $cpu = 0\n"
    "    if ($null -ne $_.CPU -and $null -ne $first[$_.Id]) { $cpu = ($_.CPU - $first[$_.Id]) / $seconds * 100 }\n"
    "    [string]::Format($culture, '{0}|{1:F1}|{2}', $_.ProcessName, $cpu, $_.WorkingSet64)\n"
    "}\n"
)


def _process_usage() -> list[tuple[float, int, str]]:
    """(CPU percent, memory bytes, name) for each process, measured over a short interval."""
    rows = []
    for line in run_script(_PROCESS_SAMPLE, timeout=30).output.splitlines():
        parts = line.split("|")
        if len(parts) == 3:
            try:
                rows.append((float(parts[1]), int(parts[2]), parts[0]))
            except ValueError:
                continue
    return rows


def system_overview(_args: dict[str, Any]) -> ToolOutcome:
    threads = psutil.cpu_count() or 1
    psutil.cpu_percent(None)
    usage = _process_usage()  # Also the sampling window for total CPU use.
    total_cpu = psutil.cpu_percent(None)

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    cpu_name = ((_registry("HKLM", r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") or {})
                .get("ProcessorNameString") or platform.processor() or "Unknown CPU").strip()
    uptime = dt.datetime.now() - dt.datetime.fromtimestamp(psutil.boot_time())
    windows = windows_version()
    lines = [
        f"Windows: {windows}",
        f"Computer: {platform.node()} · up {uptime.days}d {uptime.seconds // 3600}h {uptime.seconds % 3600 // 60}m · "
        f"Sysdoc running as administrator: {'yes' if is_admin() else 'no'}",
        f"CPU: {cpu_name} · {psutil.cpu_count(logical=False)} cores / {threads} threads · {total_cpu:.0f}% in use",
        f"Memory: {_size(memory.total)} total · {_size(memory.available)} available ({memory.percent:.0f}% used) · "
        f"page file {_size(swap.total)} ({swap.percent:.0f}% used)",
    ]
    gpus = _gpus()
    lines += [f"GPU: {gpu}" for gpu in gpus] or ["GPU: not detected"]
    for partition in psutil.disk_partitions(all=False):
        if "cdrom" in partition.opts or not partition.fstype:
            continue
        try:
            disk = psutil.disk_usage(partition.mountpoint)
        except OSError:
            continue
        lines.append(f"Drive {partition.mountpoint} ({partition.fstype}): {_size(disk.free)} free of "
                     f"{_size(disk.total)} ({disk.percent:.0f}% used)")
    try:
        battery = psutil.sensors_battery()
    except (OSError, RuntimeError):
        battery = None
    if battery:
        lines.append(f"Battery: {battery.percent:.0f}% · {'plugged in' if battery.power_plugged else 'on battery'}")
    restart = [label for label, path in (
        ("Windows Update", r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"),
        ("component servicing", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"),
    ) if _registry("HKLM", path) is not None]
    lines.append(f"Pending restart: {', '.join(restart) if restart else 'none detected'}")
    lines.append("Busiest processes (CPU): " + ", ".join(
        f"{name} {cpu:.1f}%" for cpu, _, name in sorted(usage, reverse=True)[:6]))
    lines.append("Largest processes (memory): " + ", ".join(
        f"{name} {_size(rss)}" for _, rss, name in sorted(usage, key=lambda row: row[1], reverse=True)[:6]))
    summary = " · ".join(part for part in (
        windows.split(" (")[0], cpu_name, f"{_size(memory.total)} RAM", gpus[0].split(" · ")[0] if gpus else "",
    ) if part)
    return ToolOutcome("\n".join(lines), summary)


# --- run_command -------------------------------------------------------------

def run_command(args: dict[str, Any]) -> ToolOutcome:
    command = _text_arg(args, "command")
    if not command:
        return _failure("No command was given.")
    shell = "cmd" if _text_arg(args, "shell").lower() == "cmd" else "powershell"
    result = run_script(command, shell, timeout=_int_arg(args, "timeout_seconds", 60, 5, 300))
    if result.error:
        return _failure(result.error)
    status = "Timed out" if result.timed_out else "Cancelled" if result.cancelled else f"Exit code {result.exit_code}"
    count = len(result.output.splitlines())
    return ToolOutcome(
        f"{status}.\n{clip(result.output, OUTPUT_LIMIT) or '(no output)'}",
        f"{status} · {count} {'line' if count == 1 else 'lines'} of output",
        is_error=result.timed_out or result.cancelled,
        cancelled=result.cancelled,
    )


# --- read_file ---------------------------------------------------------------

def _encoding(head: bytes) -> tuple[str, int]:
    if head.startswith(b"\xff\xfe"):
        return "utf-16-le", 2
    if head.startswith(b"\xfe\xff"):
        return "utf-16-be", 2
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8", 3
    odd = head[1:2000:2]
    if odd and odd.count(0) > len(odd) * 0.3:
        return "utf-16-le", 0
    return "utf-8", 0


def read_file(args: dict[str, Any]) -> ToolOutcome:
    raw = _text_arg(args, "path")
    if not raw:
        return _failure("No path was given.")
    path = expand_path(raw)
    if is_sensitive_path(str(path)):
        return _failure("Sysdoc doesn't read files that can hold passwords, keys, or tokens.")
    tail_lines = _int_arg(args, "tail_lines", 0, 0, 5000)
    try:
        if path.is_dir():
            return _failure(f"{path} is a folder; use list_directory instead.")
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(READ_CHUNK)
            encoding, bom = _encoding(head)
            width = 2 if encoding.startswith("utf-16") else 1
            if size <= READ_CHUNK:
                text = head[bom:].decode(encoding, "replace")
            else:
                start = size - READ_CHUNK
                handle.seek(start - start % width)
                tail = handle.read().decode(encoding, "replace")
                if tail_lines:
                    text = tail
                else:
                    beginning = head[bom:16_000].decode(encoding, "replace")
                    text = f"{beginning}\n... [{_size(size)} file, middle skipped] ...\n{tail}"
    except FileNotFoundError:
        return _failure(f"{path} doesn't exist.")
    except PermissionError:
        return _failure(f"Windows denied access to {path}.")
    except OSError as exc:
        return _failure(f"Couldn't read {path}: {exc.strerror or exc}")

    sample = text[:4000]
    if sample and sum(ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t\n\r\f") for ch in sample) > len(sample) * 0.1:
        return _failure(f"{path.name} looks like a binary file, not text.")
    if tail_lines:
        text = "\n".join(text.splitlines()[-tail_lines:])
    count = len(text.splitlines())
    return ToolOutcome(f"{path} ({_size(size)})\n{clip(text, OUTPUT_LIMIT)}", f"{count} lines from {path.name}")


# --- list_directory ----------------------------------------------------------

def _is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _is_link(entry: os.DirEntry) -> bool:
    return entry.is_symlink() or getattr(entry, "is_junction", lambda: False)()


def _entry_line(entry: os.DirEntry, is_dir: bool, root: Path) -> str:
    try:
        info = entry.stat(follow_symlinks=False)
    except OSError:
        info = None
    relative = os.path.relpath(entry.path, root)
    modified = dt.datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M") if info else "?"
    if is_dir:
        return f"{relative}{os.sep}  <folder>  {modified}"
    return f"{relative}  {_size(info.st_size) if info else '?'}  {modified}"


def list_directory(args: dict[str, Any]) -> ToolOutcome:
    raw = _text_arg(args, "path")
    if not raw:
        return _failure("No path was given.")
    root = expand_path(raw)
    if is_sensitive_path(str(root)):
        return _failure("Sysdoc doesn't list folders that hold passwords, keys, or tokens.")
    if not root.is_dir():
        return _failure(f"{root} isn't a folder or doesn't exist.")
    pattern = (_text_arg(args, "pattern") or "*").lower()
    recursive = _bool_arg(args, "recursive")
    limit = _int_arg(args, "max_entries", 100, 1, 500)
    deadline = time.monotonic() + 15
    lines: list[str] = []
    problems: list[str] = []

    def walk(folder: str, depth: int) -> bool:
        try:
            with os.scandir(folder) as iterator:
                entries = sorted(iterator, key=lambda item: (not _is_dir(item), item.name.lower()))
        except OSError as exc:
            problems.append(f"Couldn't open {folder}: {exc.strerror or exc}")
            return True
        for entry in entries:
            if len(lines) >= limit or time.monotonic() > deadline:
                return False
            if is_sensitive_path(entry.path):
                continue
            is_dir = _is_dir(entry)
            if fnmatch.fnmatch(entry.name.lower(), pattern):
                lines.append(_entry_line(entry, is_dir, root))
            if recursive and is_dir and depth < 5 and not _is_link(entry) and not walk(entry.path, depth + 1):
                return False
        return True

    complete = walk(str(root), 0)
    header = f"{root} · pattern {pattern}{' · recursive' if recursive else ''} · {len(lines)} entries"
    if not complete:
        header += " (stopped early: narrow the path or pattern to see more)"
    return ToolOutcome("\n".join([header, *lines, *problems[:10]]), f"{len(lines)} entries in {root.name or root}")


# --- get_event_log -----------------------------------------------------------

_LEVELS = {"critical": "1", "error": "1,2", "warning": "1,2,3", "information": "0,4", "all": ""}


def get_event_log(args: dict[str, Any]) -> ToolOutcome:
    log = _text_arg(args, "log") or "System"
    level = (_text_arg(args, "level") or "error").lower()
    if level not in _LEVELS:
        return _failure(f"Unknown level '{level}'. Use one of: {', '.join(_LEVELS)}.")
    hours = _int_arg(args, "hours", 24, 1, 24 * 60)
    limit = _int_arg(args, "max_events", 25, 1, 100)
    source = _text_arg(args, "source")
    contains = _text_arg(args, "contains")

    filters = [f"LogName = {ps_quote(log)}", f"StartTime = (Get-Date).AddHours(-{hours})"]
    if _LEVELS[level]:
        filters.append(f"Level = {_LEVELS[level]}")
    if source:
        filters.append(f"ProviderName = {ps_quote(source)}")
    match = f" | Where-Object {{ $_.Message -match [regex]::Escape({ps_quote(contains)}) }}" if contains else ""
    script = (
        f"$events = Get-WinEvent -FilterHashtable @{{ {'; '.join(filters)} }} -MaxEvents {1000 if contains else limit} "
        f"-ErrorAction SilentlyContinue{match} | Select-Object -First {limit}\n"
        "if (-not $events) { Write-Output 'No matching events.'; exit 0 }\n"
        "foreach ($e in $events) {\n"
        "    $text = (($e.Message -split \"`r?`n\") | Where-Object { $_.Trim() } | Select-Object -First 8) -join ' | '\n"
        "    Write-Output ('[{0:yyyy-MM-dd HH:mm:ss}] {1} · {2} · event {3}: {4}' -f "
        "$e.TimeCreated, $e.LevelDisplayName, $e.ProviderName, $e.Id, $text)\n"
        "}\n"
    )
    result = run_script(script, timeout=90)
    if result.error:
        return _failure(result.error)
    if not result.ok:
        return ToolOutcome(clip(result.output, OUTPUT_LIMIT) or result.describe(), result.describe(), is_error=True,
                           cancelled=result.cancelled)
    count = sum(line.startswith("[") for line in result.output.splitlines())
    return ToolOutcome(clip(result.output, OUTPUT_LIMIT), f"{count} {'event' if count == 1 else 'events'}")


# --- list_installed_programs -------------------------------------------------

_UNINSTALL_KEYS = (
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
)


def _programs() -> list[str]:
    found = {}
    for hive, base in _UNINSTALL_KEYS:
        for subkey in _subkeys(hive, base):
            values = _registry(hive, f"{base}\\{subkey}", "DisplayName", "DisplayVersion", "Publisher",
                               "InstallLocation", "SystemComponent") or {}
            name = str(values.get("DisplayName") or "").strip()
            if not name or values.get("SystemComponent") == 1:
                continue
            details = [str(values[key]).strip() for key in ("DisplayVersion", "Publisher", "InstallLocation")
                       if str(values.get(key) or "").strip()]
            found.setdefault(name.lower(), " · ".join([name, *details]))
    return sorted(found.values(), key=str.lower)


def _games() -> list[str]:
    games = []
    steam = (_registry("HKCU", r"Software\Valve\Steam", "SteamPath") or {}).get("SteamPath")
    if steam:
        libraries = {Path(steam)}
        try:
            folders = (Path(steam) / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
            libraries.update(Path(path.replace("\\\\", "\\")) for path in re.findall(r'"path"\s+"([^"]+)"', folders))
        except OSError:
            pass
        for library in libraries:
            for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
                try:
                    text = manifest.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                name = re.search(r'"name"\s+"([^"]+)"', text)
                folder = re.search(r'"installdir"\s+"([^"]+)"', text)
                app_id = re.search(r'"appid"\s+"(\d+)"', text)
                if name:
                    location = library / "steamapps" / "common" / folder.group(1) if folder else library
                    games.append(f"[Steam] {name.group(1)} · appid {app_id.group(1) if app_id else '?'} · {location}")
    manifests = Path(os.environ.get("ProgramData", r"C:\ProgramData"), "Epic", "EpicGamesLauncher", "Data", "Manifests")
    for item in manifests.glob("*.item"):
        try:
            data = json.loads(item.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("DisplayName"):
            games.append(f"[Epic] {data['DisplayName']} · {data.get('AppVersionString', '?')} · {data.get('InstallLocation', '?')}")
    return sorted(games, key=str.lower)


def list_installed_programs(args: dict[str, Any]) -> ToolOutcome:
    needle = _text_arg(args, "name_filter").lower()
    programs = [line for line in _programs() if needle in line.lower()]
    games = [line for line in _games() if needle in line.lower()]
    shown = programs if needle else programs[:200]
    lines = [f"Installed programs{f' matching {needle!r}' if needle else ''}: {len(programs)}"]
    lines += [f"- {line}" for line in shown]
    if len(shown) < len(programs):
        lines.append(f"... {len(programs) - len(shown)} more; use name_filter to narrow the list.")
    lines.append(f"Steam and Epic games{f' matching {needle!r}' if needle else ''}: {len(games)}")
    lines += [f"- {line}" for line in games]
    return ToolOutcome(clip("\n".join(lines), OUTPUT_LIMIT), f"{len(programs)} programs · {len(games)} games")


# --- run_health_check --------------------------------------------------------

def run_health_check(args: dict[str, Any]) -> ToolOutcome:
    available = {"network": NetworkScanner, "storage": StorageScanner}
    requested = args.get("checks") if isinstance(args.get("checks"), list) else []
    scanners = [available[name]() for name in dict.fromkeys(requested) if name in available]
    results = Orchestrator(scanners or [NetworkScanner(), StorageScanner()]).run_all()
    lines, problems = [], 0
    for result in results:
        lines.append(f"[{result.scanner_name}]")
        for finding in result.findings:
            problems += finding.severity in (Severity.WARNING, Severity.CRITICAL)
            fix = f" (suggested fix: {finding.suggested_fix})" if finding.suggested_fix else ""
            lines.append(f"- {finding.severity.value.upper()}: {finding.title}. {finding.detail}{fix}")
    summary = f"{problems} {'problem' if problems == 1 else 'problems'} found" if problems else "No problems found"
    return ToolOutcome("\n".join(lines), summary)


# --- Registry of tools -------------------------------------------------------

def _spec(name: str, description: str, properties: dict[str, Any], required: tuple[str, ...] = ()) -> ToolSpec:
    return ToolSpec(name, description, {"type": "object", "properties": properties, "required": list(required)})


def _plural_hours(hours: Any) -> str:
    return f"last {hours}h" if hours else "last 24h"


TOOLS: dict[str, Tool] = {tool.spec.name: tool for tool in (
    Tool(
        _spec("system_overview",
              "Snapshot of this PC: Windows version, CPU, memory, GPUs and driver versions, drives, uptime, "
              "pending restarts, battery, and the busiest processes. Start here for most problems.", {}),
        "System overview", system_overview, lambda args: "",
    ),
    Tool(
        _spec(RUN_COMMAND,
              "Run a READ-ONLY Windows PowerShell 5.1 (default) or cmd command to inspect the PC, e.g. Get-CimInstance, "
              "Get-ItemProperty, Get-Service, Get-AppxPackage, ipconfig /all, netsh ... show. Anything that changes the "
              "system (files, registry, settings, services, processes, network state) is refused here; put changes in "
              "propose_plan. Output is cut to about 12,000 characters, so filter with Select-Object and Where-Object. "
              "Commands run without a window or keyboard input. The user sees every command.",
              {
                  "command": {"type": "string", "description": "The command to run."},
                  "purpose": {"type": "string", "description": "Short reason shown to the user, e.g. 'Check the GPU driver version'."},
                  "shell": {"type": "string", "enum": ["powershell", "cmd"], "description": "Defaults to powershell."},
                  "timeout_seconds": {"type": "integer", "description": "Time limit in seconds (default 60, max 300)."},
              },
              ("command", "purpose")),
        "Command", run_command, lambda args: _text_arg(args, "command"),
    ),
    Tool(
        _spec("read_file",
              "Read a text file such as a game or app log, crash report, or config file. Environment variables like "
              "%LOCALAPPDATA% work. Use tail_lines for the end of long logs. Files that can hold passwords, keys, or "
              "tokens are refused.",
              {
                  "path": {"type": "string", "description": "Full path to the file."},
                  "tail_lines": {"type": "integer", "description": "Only return this many lines from the end."},
              },
              ("path",)),
        "Read file", read_file,
        lambda args: _text_arg(args, "path") + (f" · last {args['tail_lines']} lines" if args.get("tail_lines") else ""),
    ),
    Tool(
        _spec("list_directory",
              "List files and folders with sizes and modified dates, optionally filtered by a wildcard pattern and "
              "searched recursively (up to 5 levels). Useful for finding crash dumps, logs, and game install folders.",
              {
                  "path": {"type": "string", "description": "Folder to list. Environment variables work."},
                  "pattern": {"type": "string", "description": "Wildcard filter such as *.log or *.dmp (default *)."},
                  "recursive": {"type": "boolean", "description": "Search subfolders too."},
                  "max_entries": {"type": "integer", "description": "Maximum entries to return (default 100, max 500)."},
              },
              ("path",)),
        "List folder", list_directory,
        lambda args: " · ".join(part for part in (_text_arg(args, "path"), _text_arg(args, "pattern"),
                                                  "recursive" if _bool_arg(args, "recursive") else "") if part),
    ),
    Tool(
        _spec("get_event_log",
              "Read recent Windows event log entries from System, Application, or a named log such as "
              "'Microsoft-Windows-WindowsUpdateClient/Operational'. Useful for app crashes (Application Error 1000, "
              "Windows Error Reporting 1001), display driver resets (Display 4101), and unexpected shutdowns (Kernel-Power 41).",
              {
                  "log": {"type": "string", "description": "Log name (default System)."},
                  "level": {"type": "string", "enum": list(_LEVELS), "description": "Minimum severity (default error)."},
                  "hours": {"type": "integer", "description": "How far back to look (default 24)."},
                  "source": {"type": "string", "description": "Only events from this provider, e.g. 'Application Error'."},
                  "contains": {"type": "string", "description": "Only events whose message contains this text, e.g. a game's exe name."},
                  "max_events": {"type": "integer", "description": "Maximum events to return (default 25, max 100)."},
              }),
        "Event log", get_event_log,
        lambda args: " · ".join(part for part in (
            _text_arg(args, "log") or "System", _text_arg(args, "level") or "error", _plural_hours(args.get("hours")),
            _text_arg(args, "source"), f"contains {_text_arg(args, 'contains')!r}" if _text_arg(args, "contains") else "",
        ) if part),
    ),
    Tool(
        _spec("list_installed_programs",
              "List installed desktop programs (version, publisher, install folder) and Steam and Epic Games library games "
              "(including Steam app IDs). Use name_filter to narrow the results.",
              {"name_filter": {"type": "string", "description": "Only entries whose details contain this text."}}),
        "Installed programs", list_installed_programs,
        lambda args: f"matching {_text_arg(args, 'name_filter')!r}" if _text_arg(args, "name_filter") else "all",
    ),
    Tool(
        _spec("run_health_check",
              "Run Sysdoc's built-in checks: 'network' (internet connection, packet loss, latency, DNS, Roblox and Steam "
              "reachability) and 'storage' (free space on each drive).",
              {"checks": {"type": "array", "items": {"type": "string", "enum": ["network", "storage"]},
                          "description": "Which checks to run (default both)."}}),
        "Health check", run_health_check,
        lambda args: ", ".join(args["checks"]) if isinstance(args.get("checks"), list) and args["checks"] else "network, storage",
    ),
)}

ASK_USER_SPEC = _spec(
    ASK_USER,
    "Ask the user something only they can answer, such as which game is affected, the exact error message, or when the "
    "problem started. Offer choices when there are a few likely answers. Don't ask for permission to scan or to apply "
    "fixes; Sysdoc handles approvals.",
    {
        "question": {"type": "string", "description": "The question, in plain language."},
        "choices": {"type": "array", "items": {"type": "string"}, "description": "Optional suggested answers."},
    },
    ("question",),
)

PLAN_SPEC = ToolSpec(
    PROPOSE_PLAN,
    "Present a fix plan once you know the cause. Sysdoc shows the user your diagnosis, evidence, each step's explanation, "
    "and the exact script, then asks for approval. Nothing runs unless the user approves. Approved scripts run exactly as "
    "written, in order, and you receive each step's output. This is the only way to change the PC.",
    PLAN_PARAMETERS,
)

TOOL_SPECS: list[ToolSpec] = [tool.spec for tool in TOOLS.values()] + [ASK_USER_SPEC, PLAN_SPEC]
