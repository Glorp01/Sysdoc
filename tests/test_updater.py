import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from typer.testing import CliRunner

from sysdoc import __version__
from sysdoc.cli import app
from sysdoc.core import updater


PAYLOAD = b"test installer payload"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
URL = f"https://github.com/{updater.REPOSITORY}/releases/download/v0.2.10/{updater.ASSET_NAME}"


def release_data():
    return {"tag_name": "v0.2.10", "draft": False, "prerelease": False, "assets": [{
        "name": updater.ASSET_NAME, "state": "uploaded", "browser_download_url": URL,
        "digest": f"sha256:{DIGEST}", "size": len(PAYLOAD),
    }]}


def mock_api(monkeypatch, data):
    monkeypatch.setattr(updater, "_open", lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))


def test_new_release_uses_numeric_comparison(monkeypatch):
    mock_api(monkeypatch, release_data())
    release = updater.check_for_update("0.2.9")
    assert release.version == "0.2.10"
    assert release.sha256 == DIGEST
    assert release.download_url == URL


@pytest.mark.parametrize("current", ["0.2.10", "0.3.0", "1.0.0"])
def test_never_downgrades(monkeypatch, current):
    mock_api(monkeypatch, release_data())
    assert updater.check_for_update(current) is None


@pytest.mark.parametrize("field", ["draft", "prerelease"])
def test_unpublished_or_prerelease_is_ignored(monkeypatch, field):
    data = release_data()
    data[field] = True
    mock_api(monkeypatch, data)
    assert updater.check_for_update("0.2.0") is None


@pytest.mark.parametrize("version", ["v1.2.0-rc1", "1.2", "1.2.3/evil", "01.2.3", "1.2.3\n", "abc"])
def test_bad_versions_are_rejected(version):
    with pytest.raises(updater.UpdateError):
        updater.version_tuple(version)


@pytest.mark.parametrize("field,value", [
    ("browser_download_url", "https://example.com/setup.exe"),
    ("browser_download_url", URL.replace("https:", "http:")),
    ("browser_download_url", URL.replace("Glorp01", "SomeoneElse")),
    ("digest", None), ("digest", "sha256:bad"), ("state", "new"),
    ("size", 0), ("size", updater.MAX_DOWNLOAD_SIZE + 1), ("size", True),
])
def test_invalid_installer_metadata_is_rejected(monkeypatch, field, value):
    data = release_data()
    data["assets"][0][field] = value
    mock_api(monkeypatch, data)
    with pytest.raises(updater.UpdateError):
        updater.check_for_update("0.2.0")


def test_missing_installer_is_not_offered(monkeypatch):
    data = release_data()
    data["assets"] = []
    mock_api(monkeypatch, data)
    with pytest.raises(updater.UpdateError, match="no Windows installer"):
        updater.check_for_update("0.2.0")


@pytest.mark.parametrize("data", [[], {"tag_name": None}, {"tag_name": "v0.3.0", "assets": None}])
def test_malformed_response_is_handled(monkeypatch, data):
    mock_api(monkeypatch, data)
    with pytest.raises(updater.UpdateError):
        updater.check_for_update("0.2.0")


@pytest.mark.parametrize("code", [403, 429, 500])
def test_http_errors_are_friendly(monkeypatch, code):
    monkeypatch.setattr(updater, "_open", Mock(side_effect=HTTPError(URL, code, "error", {}, None)))
    with pytest.raises(updater.UpdateError, match="try again"):
        updater.check_for_update("0.2.0")


def test_no_releases_yet(monkeypatch):
    monkeypatch.setattr(updater, "_open", Mock(side_effect=HTTPError(URL, 404, "missing", {}, None)))
    assert updater.check_for_update("0.2.0") is None


def test_offline(monkeypatch):
    monkeypatch.setattr(updater, "_open", Mock(side_effect=URLError("offline")))
    with pytest.raises(updater.UpdateError, match="internet connection"):
        updater.check_for_update("0.2.0")


def test_verified_download(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(updater, "_open", lambda *a, **kw: io.BytesIO(PAYLOAD))
    progress = Mock()
    result = updater.download_update(updater.Release("0.2.10", URL, DIGEST, len(PAYLOAD)), progress)
    assert result.read_bytes() == PAYLOAD
    progress.assert_called_with(len(PAYLOAD), len(PAYLOAD))


@pytest.mark.parametrize("payload", [b"tampered installer!!!!", PAYLOAD[:-1], PAYLOAD + b"extra"])
def test_bad_download_is_removed(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(updater.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(updater, "_open", lambda *a, **kw: io.BytesIO(payload))
    with pytest.raises(updater.UpdateError):
        updater.download_update(updater.Release("0.2.10", URL, DIGEST, len(PAYLOAD)))
    assert list(tmp_path.iterdir()) == []


def test_download_rejects_external_url_before_request(monkeypatch):
    request = Mock()
    monkeypatch.setattr(updater, "_open", request)
    with pytest.raises(updater.UpdateError):
        updater.download_update(updater.Release("0.2.10", "https://example.com/evil.exe", DIGEST, 1))
    request.assert_not_called()


@pytest.mark.parametrize("url", ["http://github.com/a", "https://evil.com/a", "https://github.com@evil.com/a", "https://github.com:444/a"])
def test_untrusted_redirect_is_rejected(url):
    with pytest.raises(updater.UpdateError):
        updater._GitHubRedirectHandler().redirect_request(Request(URL), None, 302, "Found", {}, url)


def test_source_install_cannot_overwrite_python(monkeypatch):
    monkeypatch.setattr(updater.sys, "frozen", False, raising=False)
    with pytest.raises(updater.UpdateError, match="Windows installer"):
        updater.installed_directory()


def test_installer_handoff_preserves_path_and_resets_dlls(monkeypatch, tmp_path):
    install = tmp_path / "Installed App"
    install.mkdir()
    (install / "unins000.exe").touch()
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(install / "sysdoc-gui.exe"))
    monkeypatch.setattr(updater.sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    kernel = Mock()
    monkeypatch.setattr(updater.ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    launch = Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    updater.launch_installer(tmp_path / "update.exe")
    args, kwargs = launch.call_args
    assert f"/DIR={install}" in args[0]
    assert "/RESTARTAPP" in args[0]
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "shell" not in kwargs
    assert kernel.SetDllDirectoryW.call_args_list[0].args == (None,)
    assert kernel.SetDllDirectoryW.call_args_list[-1].args == (str(tmp_path / "bundle"),)


def test_cli_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_cli_check_does_not_download(monkeypatch):
    monkeypatch.setattr(updater, "check_for_update", lambda: updater.Release("0.2.10", URL, DIGEST, len(PAYLOAD)))
    download = Mock()
    monkeypatch.setattr(updater, "download_update", download)
    result = CliRunner().invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "0.2.10" in result.stdout
    download.assert_not_called()


def test_cli_declined_update_does_not_download(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "check_for_update", lambda: updater.Release("0.2.10", URL, DIGEST, len(PAYLOAD)))
    monkeypatch.setattr(updater, "installed_directory", lambda: tmp_path)
    download = Mock()
    monkeypatch.setattr(updater, "download_update", download)
    result = CliRunner().invoke(app, ["update"], input="n\n")
    assert result.exit_code == 0
    download.assert_not_called()
