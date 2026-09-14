import time
import tkinter as tk
from unittest.mock import Mock

import pytest

from sysdoc import gui
from sysdoc.gui import SysdocWindow
from sysdoc.core import config, updater
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


def test_ai_settings_save_the_provider_key_and_model(window):
    dialog = gui.AISettingsDialog(window.root)
    dialog.provider.set(dialog.labels[dialog.names.index("openai")])
    dialog._provider_changed()
    assert dialog.model.get() == "gpt-5.5"
    dialog.key.set("sk-test-123456789012")
    dialog.model.set("gpt-5.4-mini")
    assert dialog.save()
    assert config.get_provider() == "openai"
    assert config.get_api_key("openai") == "sk-test-123456789012"
    assert config.get_model("openai") == "gpt-5.4-mini"


def test_ai_settings_require_a_key(window, monkeypatch):
    error = Mock()
    monkeypatch.setattr("sysdoc.gui.messagebox.showerror", error)
    dialog = gui.AISettingsDialog(window.root)
    assert not dialog.save()
    error.assert_called_once()
    assert not config.config_file().exists()
    dialog.window.destroy()


def test_assistant_opens_in_its_own_console(window, monkeypatch):
    launch = Mock()
    monkeypatch.setattr(gui.subprocess, "Popen", launch)
    window.assistant_button.invoke()
    args, kwargs = launch.call_args
    assert args[0][-2:] == ["-m", "sysdoc"]
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_installed_app_opens_the_bundled_assistant(monkeypatch, tmp_path):
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui.sys, "executable", str(tmp_path / "sysdoc-gui.exe"))
    assert gui.assistant_command() == [str(tmp_path / "sysdoc.exe")]
