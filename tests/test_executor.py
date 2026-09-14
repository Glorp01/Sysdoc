import subprocess
import sys

import pytest

from sysdoc.agent import executor

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="runs Windows PowerShell")


def test_output_streams_and_the_exit_code_is_kept():
    lines = []
    result = executor.run_script("Write-Output 'one'; Write-Output 'two'; exit 3", on_output=lines.append)
    assert result.exit_code == 3
    assert lines == ["one", "two"]
    assert not result.ok


def test_native_exit_codes_are_reported():
    assert executor.run_script("cmd /c exit 5").exit_code == 5


def test_strict_mode_stops_at_the_first_error():
    result = executor.run_script("Get-Item 'Z:/missing'; Write-Output 'after'", strict=True)
    assert result.exit_code == 1
    assert "ERROR" in result.output
    # The error message quotes the failing line, so check for the output line itself.
    assert "after" not in result.output.splitlines()


def test_lenient_mode_continues_after_errors():
    result = executor.run_script("Get-Item 'Z:/missing'; Write-Output 'after'")
    assert result.exit_code == 0
    assert "after" in result.output.splitlines()


def test_timeouts_stop_the_script():
    result = executor.run_script("Start-Sleep -Seconds 20", timeout=1)
    assert result.timed_out
    assert result.exit_code is None
    assert result.duration < 10


def test_cmd_scripts_run():
    result = executor.run_script("echo hello", "cmd")
    assert result.ok
    assert "hello" in result.output


def test_quoting_resists_injection_including_typographic_quotes():
    text = "a'b\u2019c; Write-Output injected"
    result = executor.run_script("Write-Output " + executor.ps_quote(text))
    assert result.output == text


def test_clip_keeps_the_start_and_the_end():
    clipped = executor.clip("start" + "x" * 50_000 + "end", 1000)
    assert clipped.startswith("start")
    assert clipped.endswith("end")
    assert "omitted" in clipped
    assert len(clipped) < 1100


def run_elevation_wrapper(tmp_path, script, tamper=False):
    """Run the administrator wrapper without elevation, which exercises everything except the UAC prompt."""
    step, log = tmp_path / "step.ps1", tmp_path / "output.log"
    payload = executor._powershell_source(script, strict=True).encode("utf-8")
    step.write_bytes(payload)
    log.touch()
    wrapper = executor._elevation_wrapper(step, log, payload)
    if tamper:
        step.write_bytes(payload + b"\nWrite-Output 'injected'\n")
    completed = subprocess.run(
        [executor.powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", executor._encode(wrapper)],
        capture_output=True, timeout=60, creationflags=executor.NO_WINDOW,
    )
    return completed.returncode, log.read_text(encoding="utf-8")


def test_administrator_wrapper_runs_the_approved_script(tmp_path):
    code, output = run_elevation_wrapper(tmp_path, "Write-Output 'fixed'; cmd /c exit 4")
    assert code == 4
    assert "fixed" in output


def test_administrator_wrapper_reports_errors(tmp_path):
    code, output = run_elevation_wrapper(tmp_path, "Get-Item 'Z:/missing'")
    assert code == 1
    assert "ERROR" in output


def test_administrator_wrapper_refuses_a_script_changed_after_approval(tmp_path):
    code, output = run_elevation_wrapper(tmp_path, "Write-Output 'fixed'", tamper=True)
    assert code == 97
    assert "Refusing to run" in output
    assert "injected" not in output and "fixed" not in output
