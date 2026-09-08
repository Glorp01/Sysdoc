"""Download verified Windows updates from this project's stable GitHub releases."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sysdoc import __version__

REPOSITORY = "Glorp01/Sysdoc"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases/latest"
LATEST_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_NAME = "Sysdoc-Setup-x64.exe"
MAX_DOWNLOAD_SIZE = 250 * 1024 * 1024
VERSION_PATTERN = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", re.ASCII)


class UpdateError(RuntimeError):
    """An update could not be checked, downloaded, or installed."""


@dataclass(frozen=True)
class Release:
    version: str
    download_url: str
    sha256: str
    size: int


def version_tuple(version: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise UpdateError(f"Unsupported release version: {version!r}.")
    return tuple(int(part) for part in match.groups())


class _GitHubRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if (parsed.scheme != "https" or parsed.hostname not in {
            "github.com", "api.github.com", "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
        } or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise UpdateError("GitHub redirected the download to an unexpected address.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, *, api: bool = False):
    headers = {"User-Agent": f"Sysdoc/{__version__}", "Accept": "application/octet-stream"}
    if api:
        headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    return build_opener(_GitHubRedirectHandler()).open(Request(url, headers=headers), timeout=30)


def _network_error(exc: Exception) -> UpdateError:
    if isinstance(exc, HTTPError) and exc.code in (403, 429):
        return UpdateError("GitHub is limiting update checks. Please try again later.")
    return UpdateError("Could not contact GitHub. Check your internet connection and try again.")


def check_for_update(current_version: str = __version__) -> Release | None:
    """Return a newer, complete stable release; never install prereleases or downgrades."""
    current = version_tuple(current_version)
    try:
        with _open(LATEST_API_URL, api=True) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise UpdateError("GitHub returned too much release information.")
        data = json.loads(raw)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise _network_error(exc) from exc
    except (URLError, OSError) as exc:
        raise _network_error(exc) from exc
    except (ValueError, UnicodeError) as exc:
        raise UpdateError("GitHub returned invalid release information.") from exc

    if not isinstance(data, dict):
        raise UpdateError("GitHub returned invalid release information.")
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str):
        raise UpdateError("The release does not have a valid version.")
    if version_tuple(tag) <= current:
        return None
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{ASSET_NAME}"
    assets = data.get("assets", [])
    if not isinstance(assets, list):
        raise UpdateError("The release has invalid download information.")
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != ASSET_NAME:
            continue
        digest = asset.get("digest")
        size = asset.get("size")
        if (asset.get("browser_download_url") != expected_url
                or asset.get("state") != "uploaded"
                or not isinstance(digest, str)
                or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest)
                or type(size) is not int or not 0 < size <= MAX_DOWNLOAD_SIZE):
            raise UpdateError("The release installer is incomplete or cannot be verified. Try again later.")
        return Release(tag.removeprefix("v"), expected_url, digest[7:].lower(), size)
    raise UpdateError("The new release has no Windows installer yet. Please try again later.")


def download_update(release: Release, progress: Callable[[int, int], None] | None = None) -> Path:
    """Stage an installer and verify its size and GitHub-provided SHA-256 before use."""
    # Validate again at this execution boundary, including callers other than the UI.
    version_tuple(release.version)
    allowed_urls = {
        f"https://github.com/{REPOSITORY}/releases/download/{tag}/{ASSET_NAME}"
        for tag in (release.version, f"v{release.version}")
    }
    if (release.download_url not in allowed_urls
            or not re.fullmatch(r"[a-fA-F0-9]{64}", release.sha256)
            or type(release.size) is not int or not 0 < release.size <= MAX_DOWNLOAD_SIZE):
        raise UpdateError("Invalid update download information.")

    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="sysdoc-update-", suffix=".exe", delete=False) as target:
            staged = Path(target.name)
            digest = hashlib.sha256()
            received = 0
            with _open(release.download_url) as response:
                while chunk := response.read(256 * 1024):
                    received += len(chunk)
                    if received > release.size:
                        raise UpdateError("The update download was larger than expected.")
                    target.write(chunk)
                    digest.update(chunk)
                    if progress:
                        progress(received, release.size)
            if received != release.size or digest.hexdigest() != release.sha256.lower():
                raise UpdateError("Update verification failed. Your installed app has not been changed.")
        return staged
    except Exception as exc:
        if staged is not None:
            staged.unlink(missing_ok=True)
        if isinstance(exc, UpdateError):
            raise
        if isinstance(exc, (URLError, HTTPError)):
            raise _network_error(exc) from exc
        if isinstance(exc, OSError):
            raise UpdateError("Could not save the update. Check your connection and available disk space.") from exc
        raise


def installed_directory() -> Path:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise UpdateError("In-app updates require the Windows installer. For a Python install, download the new release wheel and run 'python -m pip install --upgrade <wheel-path>'.")
    directory = Path(sys.executable).resolve().parent
    if not (directory / "unins000.exe").is_file():
        raise UpdateError(f"Install Sysdoc once from {RELEASES_URL} to enable in-app updates.")
    return directory


def launch_installer(installer: Path, *, restart: bool = True) -> None:
    """Start an in-place upgrade; the caller must exit after successful handoff."""
    directory = installed_directory()
    command = [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
               "/CLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/UPDATE",
               f"/DIR={directory}", f"/LOG={installer.with_suffix('.log')}"]
    if restart:
        command.append("/RESTARTAPP")
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    # PyInstaller changes the DLL search path. Do not pass its bundled DLLs to Setup.
    kernel32 = ctypes.windll.kernel32
    kernel32.SetDllDirectoryW(None)
    try:
        subprocess.Popen(command, cwd=str(installer.parent), env=environment, close_fds=True)
    except OSError as exc:
        raise UpdateError("Could not start the update installer. Please try again.") from exc
    finally:
        if hasattr(sys, "_MEIPASS"):
            kernel32.SetDllDirectoryW(sys._MEIPASS)
