"""Small native desktop front end. All slow work stays off Tk's event thread."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable

from sysdoc import __version__
from sysdoc.core import config
from sysdoc.core.ai import ask_ai
from sysdoc.core.models import ScanResult
from sysdoc.core.orchestrator import Orchestrator
from sysdoc.core import updater
from sysdoc.providers import PROVIDERS
from sysdoc.scanners.network import NetworkScanner
from sysdoc.scanners.storage import StorageScanner

ACCENT = "#0ea5e9"


def assistant_command() -> list[str]:
    """The command that opens Sysdoc's terminal AI assistant."""
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return [str(executable.with_name("sysdoc.exe"))]
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return [str(executable), "-m", "sysdoc"]


def provider_label() -> str:
    name = config.get_provider()
    return PROVIDERS[name].label if name else "your AI provider"


class AISettingsDialog:
    """Choose Claude, GPT, or Gemini, and save its API key and model."""

    def __init__(self, parent: tk.Misc):
        self.saved = False
        self.names = list(PROVIDERS)
        self.labels = [f"{PROVIDERS[name].label} ({PROVIDERS[name].company})" for name in self.names]
        current = config.get_provider() or self.names[0]

        self.window = tk.Toplevel(parent)
        self.window.title("AI settings")
        self.window.resizable(False, False)
        self.window.transient(parent)
        frame = ttk.Frame(self.window, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Use your own API key from Claude, GPT, or Gemini. Usage is billed to that account.",
                  wraplength=380).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))

        ttk.Label(frame, text="Provider").grid(row=1, column=0, sticky="w", pady=4)
        self.provider = tk.StringVar(value=self.labels[self.names.index(current)])
        self.provider_choice = ttk.Combobox(frame, textvariable=self.provider, values=self.labels, state="readonly", width=34)
        self.provider_choice.grid(row=1, column=1, sticky="ew", pady=4)
        self.provider_choice.bind("<<ComboboxSelected>>", self._provider_changed)

        ttk.Label(frame, text="API key").grid(row=2, column=0, sticky="w", pady=4)
        self.key = tk.StringVar()
        ttk.Entry(frame, textvariable=self.key, show="*", width=36).grid(row=2, column=1, sticky="ew", pady=4)
        self.key_hint = tk.StringVar()
        ttk.Label(frame, textvariable=self.key_hint, foreground="#64748b").grid(row=3, column=1, sticky="w")

        ttk.Label(frame, text="Model").grid(row=4, column=0, sticky="w", pady=4)
        self.model = tk.StringVar()
        self.model_choice = ttk.Combobox(frame, textvariable=self.model, width=34)
        self.model_choice.grid(row=4, column=1, sticky="ew", pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=self.save).pack(side="right", padx=(0, 8))
        self._provider_changed()

    def selected(self) -> str:
        return self.names[self.labels.index(self.provider.get())]

    def _provider_changed(self, *_args) -> None:
        name = self.selected()
        info = PROVIDERS[name]
        self.model_choice.configure(values=[model for model, _ in info.suggested_models])
        self.model.set(config.get_model(name))
        variable = config.env_key_name(name)
        if variable:
            self.key_hint.set(f"Using {variable}. Leave blank to keep it.")
        elif config.get_api_key(name):
            self.key_hint.set("A key is saved. Leave blank to keep it.")
        else:
            self.key_hint.set(f"Get a key at {info.key_url}")

    def save(self) -> bool:
        name = self.selected()
        key = self.key.get().strip()
        if not key and not config.get_api_key(name):
            messagebox.showerror("AI settings", f"Enter your {PROVIDERS[name].label} API key.", parent=self.window)
            return False
        try:
            if key:
                config.set_api_key(name, key)
            config.set_provider(name, self.model.get().strip() or PROVIDERS[name].default_model)
        except OSError as exc:
            messagebox.showerror("Could not save settings", str(exc), parent=self.window)
            return False
        self.saved = True
        self.window.destroy()
        return True


class SysdocWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: Queue = Queue()
        self.busy = False
        self.closed = False
        self.results: list[ScanResult] = []
        self.release: updater.Release | None = None
        root.title("Sysdoc — PC & game diagnostics")
        root.geometry("900x680")
        root.minsize(700, 500)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"), foreground=ACCENT)
        style.configure("Subtitle.TLabel", font=("Segoe UI", 11), foreground="#475569")
        style.configure("TButton", padding=(12, 7))

        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        header = ttk.Frame(frame)
        header.pack(fill="x")
        ttk.Label(header, text="Sysdoc", style="Title.TLabel").pack(side="left")
        self.update_button = ttk.Button(header, text="Check for updates", command=self.update_app)
        self.update_button.pack(side="right")
        ttk.Label(header, text=f"v{__version__}", padding=(0, 0, 16, 0)).pack(side="right")
        ttk.Label(frame, text="Find and fix what's wrong with your PC, network, or games.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(4, 20))

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 12))
        self.scan_kind = tk.StringVar(value="All checks")
        self.scan_choice = ttk.Combobox(controls, textvariable=self.scan_kind,
                                        values=("All checks", "Network", "Storage"), state="readonly", width=18)
        self.scan_choice.pack(side="left", padx=(0, 8))
        self.scan_button = ttk.Button(controls, text="Run scan", command=self.scan)
        self.scan_button.pack(side="left")
        self.assistant_button = ttk.Button(controls, text="Fix a problem with AI", command=self.open_assistant)
        self.assistant_button.pack(side="right")
        self.key_button = ttk.Button(controls, text="AI settings", command=self.configure)
        self.key_button.pack(side="right", padx=(0, 8))

        self.output = ScrolledText(frame, wrap="word", font=("Segoe UI", 11), relief="solid",
                                   borderwidth=1, padx=14, pady=12, state="disabled")
        self.output.pack(fill="both", expand=True)
        for severity, color in {"ok": "#146c43", "info": "#075985", "warning": "#8a5100", "critical": "#b91c1c"}.items():
            self.output.tag_configure(severity, foreground=color)
        self.output.tag_configure("heading", font=("Segoe UI", 12, "bold"))
        self._write("Ready when you are.\n", "heading")
        self._write("Choose a scan and click Run scan. Scans run locally and don't need an AI key.\n\n"
                    "Click Fix a problem with AI to open the AI assistant. It investigates your PC with read-only "
                    "checks, shows you a fix plan with the exact commands, and changes nothing until you approve.\n\n"
                    "For quick AI answers here, open AI settings and add a Claude, GPT, or Gemini API key. Your "
                    "question and scan results are sent to that provider, and API usage may incur charges.\n")

        question_row = ttk.Frame(frame)
        question_row.pack(fill="x", pady=(14, 4))
        self.question = ttk.Entry(question_row, font=("Segoe UI", 11))
        self.question.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.question.bind("<Return>", lambda _: self.ask())
        self.ask_button = ttk.Button(question_row, text="Ask AI", command=self.ask)
        self.ask_button.pack(side="right")
        ttk.Label(frame, text="Ask a question, for example: Why does my game keep disconnecting?").pack(anchor="w")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(frame, textvariable=self.status).pack(anchor="w", pady=(14, 4))
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x")
        self.root.after(100, self._poll)

    def _write(self, text: str, tag: str = "") -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text, tag)
        self.output.configure(state="disabled")
        self.output.see("end")

    def _set_busy(self, busy: bool, status: str) -> None:
        self.busy = busy
        self.status.set(status)
        for button in (self.scan_button, self.ask_button, self.key_button, self.update_button):
            button.configure(state="disabled" if busy else "normal")
        self.scan_choice.configure(state="disabled" if busy else "readonly")
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(value=0)

    def _run(self, task: Callable, done: Callable, status: str) -> None:
        if self.busy:
            return
        self._set_busy(True, status)

        def work():
            try:
                self.events.put(("done", done, task()))
            except Exception as exc:
                self.events.put(("error", None, str(exc)))

        Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, callback, value = self.events.get_nowait()
                if kind == "progress":
                    received, total = value
                    self.status.set(f"Downloading update… {received / total:.0%}")
                else:
                    self._set_busy(False, "Ready")
                    if kind == "error":
                        self.status.set("Could not finish. Please try again.")
                        messagebox.showerror("Sysdoc", value, parent=self.root)
                    else:
                        callback(value)
                        if self.closed:
                            return
        except Empty:
            pass
        self.root.after(100, self._poll)

    def scan(self) -> None:
        choice = self.scan_kind.get()
        scanners = [NetworkScanner(), StorageScanner()] if choice == "All checks" else [
            NetworkScanner() if choice == "Network" else StorageScanner()
        ]
        self._run(lambda: Orchestrator(scanners).run_all(), self._show_results, "Running diagnostics…")

    def _show_results(self, results: list[ScanResult]) -> None:
        self.results = results
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        for result in results:
            self._write(f"{result.scanner_name.title()} scan\n", "heading")
            for finding in result.findings:
                self._write(f"{finding.severity.value.upper()} · {finding.title}\n", finding.severity.value)
                self._write(f"{finding.detail}\n")
                if finding.suggested_fix:
                    self._write(f"Try this: {finding.suggested_fix}\n")
                self._write("\n")
        self.status.set("Scan complete")

    def configure(self) -> None:
        dialog = AISettingsDialog(self.root)
        self.root.wait_window(dialog.window)
        if dialog.saved:
            self.status.set(f"AI settings saved. Using {provider_label()}.")

    def open_assistant(self) -> None:
        # Like the updater: don't let a frozen app's bundled DLL folder or environment leak into the child app.
        environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        bundle = getattr(sys, "_MEIPASS", None)
        kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None
        if kernel32:
            kernel32.SetDllDirectoryW(None)
        try:
            subprocess.Popen(assistant_command(), env=environment, close_fds=True,
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            self.status.set("Opened the AI assistant in a new window.")
        except OSError as exc:
            messagebox.showerror("Sysdoc", f"Couldn't open the AI assistant: {exc}", parent=self.root)
        finally:
            if kernel32 and bundle:
                kernel32.SetDllDirectoryW(bundle)

    def ask(self) -> None:
        question = self.question.get().strip()
        if self.busy or not question:
            return
        self._write(f"\nYou: {question}\n", "heading")
        self._run(lambda: ask_ai(question, self.results or Orchestrator([NetworkScanner(), StorageScanner()]).run_all()),
                  lambda answer: self._write(f"\nSysdoc: {answer}\n"), f"Gathering diagnostics and asking {provider_label()}…")

    def update_app(self) -> None:
        if self.release:
            self._offer_update(self.release)
        else:
            self._run(updater.check_for_update, self._offer_update, "Checking GitHub for updates…")

    def _offer_update(self, release: updater.Release | None) -> None:
        if release is None:
            self.status.set(f"You have the latest published version (v{__version__}).")
            return
        self.release = release
        self.update_button.configure(text=f"Update to {release.version}")
        try:
            updater.installed_directory()
        except updater.UpdateError as exc:
            messagebox.showinfo("Update available", f"Version {release.version} is available.\n\n{exc}", parent=self.root)
            return
        if not messagebox.askyesno("Update Sysdoc", f"Install version {release.version}?\n\n"
                                   "Sysdoc will close and reopen. Your saved API keys will be kept.", parent=self.root):
            self.status.set(f"Version {release.version} is ready when you are.")
            return
        self._run(lambda: updater.download_update(release, lambda n, total: self.events.put(("progress", None, (n, total)))),
                  self._install, "Downloading and verifying update…")

    def _install(self, path) -> None:
        try:
            updater.launch_installer(path)
        except updater.UpdateError as exc:
            messagebox.showerror("Update could not start", str(exc), parent=self.root)
            return
        self.closed = True
        self.root.destroy()

    def close(self) -> None:
        if not self.busy or messagebox.askyesno("Close Sysdoc", "Work is still in progress. Close anyway?", parent=self.root):
            self.closed = True
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    SysdocWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
