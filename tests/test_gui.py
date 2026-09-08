import time
import tkinter as tk
from unittest.mock import Mock

import pytest

from sysdoc.gui import SysdocWindow
from sysdoc.core import updater
from sysdoc.core.models import Finding, ScanResult, Severity


@pytest.fixture
def window():
    root = tk.Tk()
    root.withdraw()
    window = SysdocWindow(root)
    yield window
    if not window.closed:
        root.destroy()


def finish_work(window):
    deadline = time.monotonic() + 5
    while window.busy and time.monotonic() < deadline:
        window.root.update()
        time.sleep(0.01)
    assert not window.busy


def test_scan_renders_findings_without_blocking(window, monkeypatch):
    findings = [ScanResult("storage", [Finding("Drive nearly full", Severity.WARNING, "4 GB free", "Remove old files")])]
    monkeypatch.setattr("sysdoc.gui.Orchestrator.run_all", lambda _: findings)
    window.scan_kind.set("Storage")
    window.scan_button.invoke()
    assert window.busy
    finish_work(window)
    text = window.output.get("1.0", "end")
    assert "Drive nearly full" in text
    assert "Remove old files" in text
    assert window.results == findings


def test_update_button_checks_in_background(window, monkeypatch):
    monkeypatch.setattr(updater, "check_for_update", lambda: None)
    window.update_button.invoke()
    finish_work(window)
    assert "latest published version" in window.status.get()


def test_update_error_restores_controls(window, monkeypatch):
    monkeypatch.setattr(updater, "check_for_update", Mock(side_effect=updater.UpdateError("Offline")))
    error = Mock()
    monkeypatch.setattr("sysdoc.gui.messagebox.showerror", error)
    window.update_button.invoke()
    finish_work(window)
    assert not window.update_button.instate(["disabled"])
    assert error.call_args.args[1] == "Offline"


def test_declining_update_keeps_it_available(window, monkeypatch, tmp_path):
    release = updater.Release("0.3.0", "unused", "unused", 100)
    monkeypatch.setattr(updater, "installed_directory", lambda: tmp_path)
    monkeypatch.setattr("sysdoc.gui.messagebox.askyesno", lambda *a, **k: False)
    download = Mock()
    monkeypatch.setattr(updater, "download_update", download)
    window._offer_update(release)
    assert window.release == release
    assert window.update_button.cget("text") == "Update to 0.3.0"
    download.assert_not_called()


def test_successful_update_handoff_closes_window_without_tk_errors(window, monkeypatch, tmp_path):
    launch = Mock()
    monkeypatch.setattr(updater, "launch_installer", launch)
    path = tmp_path / "installer.exe"
    window.events.put(("done", window._install, path))
    window._poll()
    launch.assert_called_once_with(path)
    assert window.closed


def test_failed_installer_handoff_keeps_app_open(window, monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "launch_installer", Mock(side_effect=updater.UpdateError("Could not start")))
    monkeypatch.setattr("sysdoc.gui.messagebox.showerror", Mock())
    window._install(tmp_path / "installer.exe")
    assert not window.closed
