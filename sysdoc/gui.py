"""Small native desktop front end. All slow work stays off Tk's event thread."""
from __future__ import annotations

from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable

from sysdoc import __version__
from sysdoc.core.ai import ask_ai
from sysdoc.core.config import save_api_key
from sysdoc.core.models import ScanResult
from sysdoc.core.orchestrator import Orchestrator
from sysdoc.core import updater
from sysdoc.scanners.network import NetworkScanner
from sysdoc.scanners.storage import StorageScanner


class SysdocWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: Queue = Queue()
        self.busy = False
        self.closed = False
        self.results: list[ScanResult] = []
        self.release: updater.Release | None = None
        root.title("Sysdoc — PC & game diagnostics")
        root.geometry("880x660")
        root.minsize(680, 480)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"))
        style.configure("TButton", padding=(12, 7))

        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        header = ttk.Frame(frame)
        header.pack(fill="x")
        ttk.Label(header, text="Sysdoc", style="Title.TLabel").pack(side="left")
        self.update_button = ttk.Button(header, text="Check for updates", command=self.update_app)
        self.update_button.pack(side="right")
        ttk.Label(header, text=f"v{__version__}", padding=(0, 0, 16, 0)).pack(side="right")
        ttk.Label(frame, text="Find out what's slowing down your PC, network, or games.").pack(anchor="w", pady=(4, 20))

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 12))
        self.scan_kind = tk.StringVar(value="All checks")
        self.scan_choice = ttk.Combobox(controls, textvariable=self.scan_kind,
                                      values=("All checks", "Network", "Storage"), state="readonly", width=18)
        self.scan_choice.pack(side="left", padx=(0, 8))
        self.scan_button = ttk.Button(controls, text="Run scan", command=self.scan)
        self.scan_button.pack(side="left")
        self.key_button = ttk.Button(controls, text="Set AI key", command=self.configure)
        self.key_button.pack(side="right")

        self.output = ScrolledText(frame, wrap="word", font=("Segoe UI", 11), relief="solid",
                                   borderwidth=1, padx=14, pady=12, state="disabled")
        self.output.pack(fill="both", expand=True)
        for severity, color in {"ok": "#146c43", "info": "#075985", "warning": "#8a5100", "critical": "#b91c1c"}.items():
            self.output.tag_configure(severity, foreground=color)
        self.output.tag_configure("heading", font=("Segoe UI", 12, "bold"))
        self._write("Ready when you are.\n", "heading")
        self._write("Choose a scan and click Run scan. Scans run locally and do not need an AI key.\n\n"
                    "For AI help, save your Gemini API key, then ask a question below. "
                    "Your question and scan results will be sent to Google Gemini; API usage may incur charges.\n")

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
        key = simpledialog.askstring("AI setup", "Gemini API key (saved on this PC):", show="*", parent=self.root)
        if key and key.strip():
            try:
                save_api_key(key.strip())
                self.status.set("AI key saved")
            except OSError as exc:
                messagebox.showerror("Could not save key", str(exc), parent=self.root)

    def ask(self) -> None:
        question = self.question.get().strip()
        if self.busy or not question:
            return
        self._write(f"\nYou: {question}\n", "heading")
        self._run(lambda: ask_ai(question, self.results or Orchestrator([NetworkScanner(), StorageScanner()]).run_all()),
                  lambda answer: self._write(f"\nSysdoc: {answer}\n"), "Gathering diagnostics and asking Gemini…")

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
                                   "Sysdoc will close and reopen. Your saved API key will be kept.", parent=self.root):
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
