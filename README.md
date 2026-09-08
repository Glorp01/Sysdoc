# Sysdoc

A Windows desktop and command-line assistant that checks your PC, network, and games, then explains what to fix. Local scans work without an account or API key. Optional Gemini troubleshooting uses your real scan results.

## Install on Windows

1. [Download the latest Sysdoc installer](https://github.com/Glorp01/Sysdoc/releases/latest/download/Sysdoc-Setup-x64.exe).
2. Run **Sysdoc-Setup-x64.exe**. It installs for your Windows account; Python and administrator access are not required.
3. Open **Sysdoc** from the Start menu. You can also choose a desktop shortcut during setup.

Requires Windows 10 or 11, x64. The installer is currently unsigned, so Windows may show an unknown-publisher warning. Download it from this repository's [GitHub Releases](https://github.com/Glorp01/Sysdoc/releases) page.

## Update without downloading another installer yourself

Click **Check for updates** at the top of the app. When an update is available, confirm **Yes**. Sysdoc downloads and verifies the update, closes, upgrades the existing installation, and reopens. Your saved Gemini API key stays in `%USERPROFILE%\.sysdoc\config.json`.

Updates come from published stable releases of `Glorp01/Sysdoc`. Downloads must match the release's SHA-256 digest and size before the installer can run. Failed downloads leave the installed app untouched. Updates require an internet connection and are installed only after you confirm them.

Existing users of the old standalone `sysdoc.exe` need to run the new installer once to get the desktop app and in-app updates. Python/source installations continue to use pip for upgrades.

## Use the desktop app

- Choose **All checks**, **Network**, or **Storage**, then click **Run scan**.
- Results show severity, an explanation, and suggested fixes.
- For AI help, click **Set AI key** and save a [Gemini API key](https://aistudio.google.com/apikey). Enter a question and click **Ask AI**.

AI requests send your question and scan results to Google Gemini and may incur API charges. `GEMINI_API_KEY`, if set, takes precedence over the saved key. Scans run locally and do not require AI.

## Command line

The Windows installer also includes `%LOCALAPPDATA%\Programs\Sysdoc\sysdoc.exe` (or your chosen installation folder). Add that folder to your `PATH` if you want to type `sysdoc` from any terminal.

| Command | What it does |
| --- | --- |
| `sysdoc gui` | Open the desktop window |
| `sysdoc scan network` | Ping, packet loss, DNS, and game-service reachability |
| `sysdoc scan storage` | Drive capacity and free-space warnings |
| `sysdoc scan all` | Run every scanner |
| `sysdoc ask "why does my game disconnect"` | Scan and ask Gemini for help |
| `sysdoc configure` | Save your Gemini API key |
| `sysdoc --version` | Show the current version |
| `sysdoc update --check` | Check GitHub without installing |
| `sysdoc update` | Confirm, download, and apply an update |
| `sysdoc update --yes` | Apply an available update without a prompt |

## Install from source

Python 3.11+ with Tk is required. For development on Windows:

```powershell
git clone https://github.com/Glorp01/Sysdoc.git
cd Sysdoc
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
sysdoc gui
```

Alternatively, download the wheel from a release and install it with `python -m pip install <wheel-path>`. Upgrade with `python -m pip install --upgrade <new-wheel-path>`. Source users should pull the latest code and reinstall with `python -m pip install -e .`.

## Publish an update

**Push changes to `main`.** The [Windows release workflow](.github/workflows/release.yml) automatically:

1. Assigns a version using the series in `sysdoc/__init__.py` plus the workflow run number. With a base of `0.2.0`, run 1 produces `0.2.1`, run 2 produces `0.2.2`, and so on.
2. Runs the tests, builds the desktop app and CLI, and creates a per-user Windows installer.
3. Tests installation, an in-place upgrade, installed CLI diagnostics, settings preservation, and uninstallation on a disposable Windows runner.
4. Uploads the installer, wheel, source distribution, and checksums to a draft GitHub Release, then publishes it after all uploads finish.

Installed users can then click **Check for updates**. No manual tags, version edits, or artifact uploads are needed for routine updates. Pull requests build and test without publishing. You can also run the workflow manually from the Actions tab on `main`.

To start a new release series, change the base version in `sysdoc/__init__.py`, for example to `0.3.0`. Keep the series increasing and preserve the workflow file/run counter. Re-running a completed release does not replace its published downloads. To roll back a faulty change, revert the code and push a new version; the updater will not downgrade users.

The build job uses read-only repository access; only the publish job has `contents: write`. The workflow uses GitHub's built-in token and requires GitHub Actions to be enabled. Keep the repository public so installed apps can check and download releases without credentials.

## Build locally

Use Windows x64, Python 3.13, and [Inno Setup 6](https://jrsoftware.org/isinfo.php). `requirements-build.txt` pins the tested Windows build dependencies.

```powershell
python -m pip install -r requirements-build.txt
python -m pip install -e . --no-deps
python -m pytest -q
./scripts/build_windows.ps1
```

Release artifacts are written to `dist/release/`. The frozen executables are in `dist/windows/`. The installer smoke test is intended for disposable CI runners, since it registers and uninstalls a real app.

## How it works

Each scanner returns `Finding` objects. `Orchestrator` collects them into scan results, which the desktop app and CLI display. The optional AI layer receives those same results as context.

To add a scanner, subclass `Scanner` in `sysdoc/scanners/`, implement `run() -> list[Finding]`, and register it in the CLI and desktop scanner lists.

## Known limitations

- Ping parsing currently targets English-language Windows.
- Drive health is based on free space; SMART data is not read yet.
- Game checks cover Roblox/Steam network reachability, not game logs or crash files.
- In-app installation supports the Windows installer distribution; pip and source users update through their Python environment.
