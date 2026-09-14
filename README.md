# Sysdoc

Sysdoc is an AI repair assistant for Windows. Describe what's wrong, such as a game that crashes, Wi-Fi that keeps dropping, or a PC that suddenly got slow. Sysdoc investigates your PC, explains what it found, and proposes a fix plan showing the exact commands it wants to run. Nothing changes until you approve.

It uses your own API key for **Claude** (Anthropic), **GPT** (OpenAI), or **Gemini** (Google). Quick local scans work without a key.

## How it works

1. **Describe the problem.** Run `sysdoc fix "Valorant crashes when a match starts"`, or run `sysdoc` and type it in.
2. **Allow scanning.** The first time the assistant needs to look at your PC, Sysdoc asks. Allow it for this session, always, before every check, or not at all.
3. **The assistant investigates** with read-only checks: system details and driver versions, event logs, game and app logs, installed programs and games, services, disk space, and network tests. Each check is shown as it runs.
4. **Review the fix plan.** Sysdoc shows the diagnosis, the evidence, and every step with an explanation, a risk level, whether it needs administrator rights, and the exact PowerShell script.
5. **Decide.** Run all steps, go step by step, request changes, or cancel. Approved scripts run exactly as shown, with live output. Steps that need administrator rights show a Windows permission prompt.
6. **The assistant verifies the fix** where it can and summarizes what changed and anything left for you to do.

### Safety and privacy

- Investigation commands must only read. Sysdoc checks each command before it runs. Commands that would change files, settings, services, or running programs are refused and must be proposed in a fix plan. Commands Sysdoc can't verify need your explicit approval, even when scanning is allowed.
- Commands and files involving passwords, keys, browser credentials, or Sysdoc's own settings are always blocked.
- An approved plan can't be changed before it runs. Administrator steps are verified against the approved script first.
- Every step that runs is recorded in `%USERPROFILE%\.sysdoc\history.jsonl`. Run `sysdoc history` to review them.
- What the assistant reads (system details, command output, log excerpts) is sent to the AI provider you chose. API usage is billed to your provider account.

These checks are a safety net, not a sandbox. Read each plan before you approve it.

## Run the terminal app on Windows

1. [Download the latest terminal app](https://github.com/Glorp01/Sysdoc/releases/latest/download/Sysdoc-Terminal-x64.exe).
2. Open PowerShell in the download folder and run `./Sysdoc-Terminal-x64.exe`.
3. Run `./Sysdoc-Terminal-x64.exe setup` to configure Claude, GPT, or Gemini.

Requires Windows 10 or 11, x64. The executable is unsigned, so Windows may show an unknown-publisher warning. Download it from the [GitHub Releases](https://github.com/Glorp01/Sysdoc/releases) page.

## Connect an AI provider

Run `sysdoc setup`. Running `sysdoc` for the first time also starts setup. Choose a provider, paste your API key, and pick a model. Sysdoc checks the key before saving it.

| Provider | Get a key | Default model | Environment variable |
| --- | --- | --- | --- |
| Claude (Anthropic) | [platform.claude.com](https://platform.claude.com/settings/keys) | `claude-opus-5` | `ANTHROPIC_API_KEY` |
| GPT (OpenAI) | [platform.openai.com](https://platform.openai.com/api-keys) | `gpt-5.5` | `OPENAI_API_KEY` |
| Gemini (Google) | [aistudio.google.com](https://aistudio.google.com/apikey) | `gemini-3.5-flash` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |

- Settings and keys are saved in `%USERPROFILE%\.sysdoc\config.json`. Environment variables take precedence over saved keys.
- `SYSDOC_PROVIDER` (`claude`, `gpt`, or `gemini`) chooses a provider without changing saved settings.
- Any model your key can access works. `sysdoc models` lists them; switch with `--model` or `/model`.
- `OPENAI_BASE_URL` points the GPT provider at an OpenAI-compatible server.
- Gemini keys saved by Sysdoc 0.2 keep working.

## Use the assistant

Start it with `sysdoc` or `sysdoc fix "describe the problem"`, then chat normally. Press Ctrl+C to interrupt the assistant or a running step. Inside the assistant:

| Command | What it does |
| --- | --- |
| `/new` | Start a new conversation |
| `/scan` | Run quick network and storage checks |
| `/provider` | Switch between Claude, GPT, and Gemini |
| `/model` | Change the AI model |
| `/permissions` | Change whether Sysdoc may scan your PC |
| `/history` | Show fixes Sysdoc has run |
| `/setup` | Enter or change an API key |
| `/exit` | Quit |

Run `sysdoc fix --plan-only "..."` to get a diagnosis and plan without running anything. Run Sysdoc from an administrator terminal to avoid a permission prompt for each administrator step.

## Command line

The downloaded executable can be run directly from PowerShell. Optionally rename it to `sysdoc.exe` and place it in a folder on your `PATH`.

| Command | What it does |
| --- | --- |
| `sysdoc` | Start the AI repair assistant |
| `sysdoc fix "my game crashes on launch"` | Investigate and fix a problem, then keep chatting |
| `sysdoc ask "why is my ping high"` | Quick advice from local scan results; the AI can't run anything |
| `sysdoc scan` | Run every local check (also `scan network`, `scan storage`) |
| `sysdoc setup` | Choose a provider and save its API key |
| `sysdoc config` | Show providers, models, keys (masked), and scan permission |
| `sysdoc models` | List the models your API key can use |
| `sysdoc history` | Show fix steps Sysdoc has run |
| `sysdoc --version` | Show the current version |
| `sysdoc update` | Show where to download the latest terminal executable |

`--provider` and `--model` work with `sysdoc`, `fix`, and `ask`, for example `sysdoc --provider gemini`.

## Update

Download the latest `Sysdoc-Terminal-x64.exe` from the [GitHub Releases](https://github.com/Glorp01/Sysdoc/releases/latest) page. Your settings and API keys stay in `%USERPROFILE%\.sysdoc\config.json`.

## Install from source

Python 3.11+ with Tk is required. For development on Windows:

```powershell
git clone https://github.com/Glorp01/Sysdoc.git
cd Sysdoc
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
sysdoc
```

Alternatively, download the wheel from a release and install it with `python -m pip install <wheel-path>`. Upgrade with `python -m pip install --upgrade <new-wheel-path>`. Source users should pull the latest code and reinstall with `python -m pip install -e .`.

## Publish an update

**Push changes to `main`.** The [Windows release workflow](.github/workflows/release.yml) automatically:

1. Assigns a version using the series in `sysdoc/__init__.py` plus the workflow run number. With a base of `0.3.0`, run 20 produces `0.3.20`.
2. Runs the tests and builds the standalone terminal executable and Python packages.
3. Uploads the terminal executable, wheel, source distribution, and checksums to a draft GitHub Release, then publishes it after all uploads finish.

Users can download the terminal executable from the published release. No manual tags, version edits, or artifact uploads are needed for routine updates. Pull requests build and test without publishing. You can also run the workflow manually from the Actions tab on `main`.

To start a new release series, change the base version in `sysdoc/__init__.py`, for example to `0.4.0`. Keep the series increasing and preserve the workflow file/run counter. Re-running a completed release does not replace its published downloads. To roll back a faulty change, revert the code and push a new version; the updater will not downgrade users.

The build job uses read-only repository access; only the publish job has `contents: write`. The workflow uses GitHub's built-in token and requires GitHub Actions to be enabled.

## Build locally

Use Windows x64 and Python 3.13. `requirements-build.txt` pins the tested Windows build dependencies.

```powershell
python -m pip install -r requirements-build.txt
python -m pip install -e . --no-deps
python -m pytest -q
./scripts/build_windows.ps1
```

Release artifacts are written to `dist/release/`. The standalone terminal executable is `dist/release/Sysdoc-Terminal-x64.exe`.

## Project layout

| Path | Purpose |
| --- | --- |
| `sysdoc/agent/session.py` | The repair conversation: tool calls, scan permission, plan approval, and step execution |
| `sysdoc/agent/tools.py` | Read-only investigation tools the AI can call |
| `sysdoc/agent/safety.py` | Classifies commands as read-only, changing, unverifiable, or sensitive |
| `sysdoc/agent/plan.py` | The fix plan schema and validation |
| `sysdoc/agent/executor.py` | Runs PowerShell with streamed output, timeouts, and UAC elevation |
| `sysdoc/agent/prompts.py`, `audit.py` | The system prompt and the local history of executed steps |
| `sysdoc/providers/` | Claude, GPT, and Gemini behind one tool-calling interface |
| `sysdoc/ui/` | The terminal theme and interface |
| `sysdoc/scanners/` | Local health checks that return `Finding` objects |
| `sysdoc/cli.py`, `sysdoc/gui.py` | Command-line and desktop entry points |

To add an investigation tool, write a read-only function that returns a `ToolOutcome` and register it in `TOOLS` in `sysdoc/agent/tools.py`. To add a scanner, subclass `Scanner` in `sysdoc/scanners/`, implement `run() -> list[Finding]`, and register it in the CLI and desktop scanner lists.

## Known limitations

- Windows only. Fix scripts target Windows PowerShell 5.1.
- The read-only check recognises common Windows diagnostics; less common tools need your approval.
- Stopping an administrator step with Ctrl+C may not end it immediately.
- Ping parsing currently targets English-language Windows.
- Drive health is based on free space; SMART data is not read yet.
- The Windows release is a standalone terminal executable; pip and source users update through their Python environment.
