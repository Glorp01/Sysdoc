# sysdoc

A CLI assistant that diagnoses problems with your PC, network, and games — then explains them in plain English.

`sysdoc` runs real diagnostic checks locally (fast and free), then optionally hands the results to Google's Gemini so you get an actual explanation and fix plan instead of a wall of raw numbers.

```
$ sysdoc scan all

network scan
● Network latency normal
   Average ping to 1.1.1.1 is 23ms.
● DNS resolution working
   Successfully resolved: roblox.com, steamcommunity.com, google.com
● Roblox reachable
   Successfully connected to roblox.com:443.

storage scan
● Drive C:\ has healthy free space
   136.7GB free of 475.8GB (71% used).
● Drive D:\ is getting full
   58.1GB free of 931.5GB (94% used).
   Fix: Uninstall unused games or move large files to another drive.
```

## Features

- **Network diagnostics** — ping/latency, packet loss, DNS resolution, and TCP reachability to game services (Roblox, Steam)
- **Storage diagnostics** — free space and capacity warnings across every drive, tuned for game installs and updates
- **AI troubleshooting** — ask free-form questions and get answers grounded in your actual scan results, not generic advice
- **Severity-ranked findings** — every check reports OK / INFO / WARNING / CRITICAL with a suggested fix

## Requirements

- Windows 10 or 11
- Python 3.11+ (not needed if you use the standalone `.exe`)
- A Gemini API key for the `ask` command — free tier available at [aistudio.google.com/apikey](https://aistudio.google.com/apikey). Scans work without one.

## Installation

### Option 1 — Standalone executable (no Python needed)

Download `sysdoc.exe` from the release, put it somewhere on your `PATH`, and run it. This is the easiest option if you just want to use the tool.

### Option 2 — Install the wheel

```powershell
pip install sysdoc-0.1.0-py3-none-any.whl
```

### Option 3 — From source

```powershell
git clone <repo-url>
cd F.R.I.D.A.Y
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## Setup

Scans work immediately with no configuration. To enable the `ask` command, save your Gemini API key once:

```powershell
sysdoc configure
```

The prompt hides your input, and the key is stored in `~/.sysdoc/config.json` — never in the project folder, so it can't be committed by accident. You can also set the `GEMINI_API_KEY` environment variable instead, which takes precedence.

## Usage

| Command | What it does |
| --- | --- |
| `sysdoc scan network` | Ping, packet loss, DNS, and game-service reachability |
| `sysdoc scan storage` | Free space and capacity warnings for every drive |
| `sysdoc scan all` | Every scanner in one pass |
| `sysdoc ask "<question>"` | Runs all scans, then answers your question using the results as context |
| `sysdoc configure` | Save your Gemini API key |

Examples:

```powershell
sysdoc scan network
sysdoc ask "why does roblox keep disconnecting me"
sysdoc ask "is my drive too full to install a 90gb game"
```

Note that `ask` makes a billed Gemini API call each time (small, but not free).

## How it works

```
scanners/          each check returns Finding objects
    ↓
Orchestrator       runs every scanner, collects ScanResults
    ↓
CLI                prints findings, color-coded by severity
    ↓
core/ai.py         optionally sends findings + your question to Gemini
```

Every scanner implements one method — `run() -> list[Finding]` — so the CLI, orchestrator, and AI layer never need scanner-specific code.

### Adding a new scanner

1. Create `sysdoc/scanners/yourthing.py` with a class subclassing `Scanner`, setting `name` and implementing `run()`.
2. Return `Finding` objects with a `title`, `severity`, `detail`, and optional `suggested_fix`.
3. Add it to `_all_scanners()` in `sysdoc/cli.py`.

That's it — it automatically appears in `scan all` and gets fed to the AI layer as context.

## Building a release

```powershell
# Wheel + source distribution
pip install build
python -m build

# Standalone Windows executable
pip install pyinstaller
pyinstaller --onefile --name sysdoc sysdoc/cli.py
```

Artifacts land in `dist/`.

## Known limitations

- Ping output parsing targets English-language Windows; other locales may report latency as unparsed.
- Drive health is based on free space only — SMART status is not read yet.
- Roblox and Steam are checked for network reachability only; log/crash-file parsing is not implemented yet.

## Roadmap

- Roblox log and crash-file parsing for known error signatures
- Steam log parsing (download corruption, disk write errors, VAC issues)
- SMART drive health via WMI
- GPU driver and DirectX checks
