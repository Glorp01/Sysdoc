"""Sysdoc's command line: an interactive AI repair assistant plus quick local scans."""
from __future__ import annotations

import io
import sys
import time

import typer
from rich.table import Table
from rich.text import Text

from sysdoc import __version__
from sysdoc.core import config
from sysdoc.core.ai import ask_ai, configured_provider
from sysdoc.core.orchestrator import Orchestrator
from sysdoc.providers import PROVIDERS, Provider, ProviderError, create_provider, resolve_provider_name
from sysdoc.scanners.base import Scanner
from sysdoc.scanners.network import NetworkScanner
from sysdoc.scanners.storage import StorageScanner
from sysdoc.ui.terminal import TerminalUI, print_banner, print_scan_results, print_welcome
from sysdoc.ui.theme import make_console

# Legacy Windows consoles default to cp1252, which can't encode the interface's symbols.
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

console = make_console()

app = typer.Typer(
    name="sysdoc",
    help="AI repair assistant for your PC. Run 'sysdoc' to start, or 'sysdoc fix \"what's wrong\"'.",
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
)
scan_app = typer.Typer(help="Run quick local health checks. No AI or API key needed.", invoke_without_command=True)
app.add_typer(scan_app, name="scan")

PROVIDER_HELP = "AI provider for this session: claude, gpt, or gemini."
MODEL_HELP = "Model ID for this session, such as claude-sonnet-5 or gpt-5.5."
COMMANDS = (
    ("/new", "Start a new conversation"),
    ("/scan", "Run quick network and storage checks"),
    ("/provider", "Switch between Claude, GPT, and Gemini"),
    ("/model", "Change the AI model"),
    ("/permissions", "Change whether Sysdoc may scan your PC"),
    ("/history", "Show fixes Sysdoc has run"),
    ("/setup", "Enter or change an API key"),
    ("/exit", "Quit"),
)


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show the installed version.", is_eager=True),
    provider: str | None = typer.Option(None, "--provider", "-p", help=PROVIDER_HELP),
    model: str | None = typer.Option(None, "--model", "-m", help=MODEL_HELP),
) -> None:
    if version:
        console.print(f"sysdoc {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _interactive(provider, model, first_message=None, plan_only=False)


@app.command("fix")
def fix(
    problem: list[str] = typer.Argument(..., help="What's wrong, e.g. \"Valorant crashes when a match starts\"."),
    provider: str | None = typer.Option(None, "--provider", "-p", help=PROVIDER_HELP),
    model: str | None = typer.Option(None, "--model", "-m", help=MODEL_HELP),
    plan_only: bool = typer.Option(False, "--plan-only", help="Diagnose and show a fix plan without running it."),
) -> None:
    """Diagnose and fix a problem, then keep chatting for follow-ups."""
    _interactive(provider, model, first_message=" ".join(problem), plan_only=plan_only)


@app.command("ask")
def ask(
    question: list[str] = typer.Argument(..., help="A question, e.g. \"why is my ping so high\"."),
    provider: str | None = typer.Option(None, "--provider", "-p", help=PROVIDER_HELP),
    model: str | None = typer.Option(None, "--model", "-m", help=MODEL_HELP),
) -> None:
    """Get quick advice using local scan results. The AI can't run anything."""
    try:
        with console.status("Running quick checks…", spinner="dots"):
            results = Orchestrator(_all_scanners()).run_all()
        with console.status("Thinking…", spinner="dots"):
            answer = ask_ai(" ".join(question), results, provider=provider, model=model)
    except ProviderError as exc:
        TerminalUI(console).notice(str(exc), "error")
        raise typer.Exit(code=1)
    TerminalUI(console).assistant_message(answer)


@app.command("setup")
def setup() -> None:
    """Choose an AI provider (Claude, GPT, or Gemini) and save its API key."""
    if _setup_wizard(TerminalUI(console)) is None:
        raise typer.Exit(code=1)


@app.command("configure", hidden=True)
def configure() -> None:
    """Alias for setup, kept for Sysdoc 0.2 users."""
    setup()


@app.command("config")
def show_config() -> None:
    """Show the AI provider, models, API keys, and scan permission."""
    settings = config.load_config()
    active = config.get_provider(settings)
    table = Table(box=None, header_style="muted", padding=(0, 2))
    for column in ("", "Provider", "Model", "API key"):
        table.add_column(column)
    for name, info in PROVIDERS.items():
        key = config.get_api_key(name, settings)
        variable = config.env_key_name(name)
        source = f"from {variable}" if variable else "saved" if key else ""
        table.add_row(
            Text("●", style="ok") if name == active else "",
            info.label,
            config.get_model(name, settings),
            Text(f"{config.mask_key(key)} ({source})") if key else Text("not set", style="muted"),
        )
    console.print()
    console.print(table)
    scan_mode = {"auto": "always allowed", "ask": "ask each time"}.get(config.get_scan_mode(settings) or "", "ask each session")
    console.print(Text.assemble(("\n  Scan permission: ", "muted"), scan_mode))
    console.print(Text.assemble(("  Settings file: ", "muted"), str(config.config_file())), soft_wrap=True)


@app.command("models")
def models(provider: str | None = typer.Option(None, "--provider", "-p", help=PROVIDER_HELP)) -> None:
    """List the models your API key can use."""
    try:
        client = configured_provider(provider)
        with console.status(f"Asking {PROVIDERS[client.name].label} for models…", spinner="dots"):
            available = client.list_models()
    except ProviderError as exc:
        TerminalUI(console).notice(str(exc), "error")
        raise typer.Exit(code=1)
    console.print()
    for model_id in available:
        current = model_id == client.model
        console.print(Text.assemble(("  ● " if current else "    ", "ok"), (model_id, "bold" if current else "")))
    console.print(Text(f"\n  Use one with: sysdoc --provider {client.name} --model <id>, or /model inside Sysdoc.", style="muted"))


@app.command("history")
def history(limit: int = typer.Option(15, "--limit", "-n", help="How many steps to show.")) -> None:
    """Show fix steps Sysdoc has run on this PC."""
    _print_history(limit)


@app.command("gui")
def gui() -> None:
    """Open the desktop app."""
    from sysdoc.gui import main as open_gui
    open_gui()


@app.command("update")
def update(
    check: bool = typer.Option(False, "--check", help="Check without installing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Install without a confirmation prompt."),
) -> None:
    """Explain how to update the standalone terminal executable."""
    del check, yes
    console.print(
        "Terminal releases are standalone executables. Download the latest "
        "Sysdoc-Terminal-x64.exe from https://github.com/Glorp01/Sysdoc/releases/latest."
    )


# --- Scans -------------------------------------------------------------------

def _all_scanners() -> list[Scanner]:
    return [NetworkScanner(), StorageScanner()]


def _run_scan(scanners: list[Scanner]) -> None:
    with console.status("Running checks…", spinner="dots"):
        results = Orchestrator(scanners).run_all()
    print_scan_results(console, results)


@scan_app.callback()
def scan_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _run_scan(_all_scanners())


@scan_app.command("network")
def scan_network() -> None:
    """Check internet connection, packet loss, latency, DNS, and game services."""
    _run_scan([NetworkScanner()])


@scan_app.command("storage")
def scan_storage() -> None:
    """Check free space on each drive."""
    _run_scan([StorageScanner()])


@scan_app.command("all")
def scan_all() -> None:
    """Run every available check."""
    _run_scan(_all_scanners())


# --- Setup -------------------------------------------------------------------

def _pick_provider(ui: TerminalUI, current: str | None) -> str:
    names = list(PROVIDERS)
    options = [
        (str(number), f"{PROVIDERS[name].label} ({PROVIDERS[name].company}){' · current' if name == current else ''}")
        for number, name in enumerate(names, 1)
    ]
    return names[int(ui.choose(options)) - 1]


def _pick_model(ui: TerminalUI, provider: str, current: str) -> str | None:
    """Offer suggested models; returns None when the user keeps the current one."""
    suggestions = PROVIDERS[provider].suggested_models
    for number, (model_id, note) in enumerate(suggestions, 1):
        marker = " · current" if model_id == current else ""
        console.print(Text.assemble((f"   {number} ", "accent"), (model_id, "bold"), (f"  {note}{marker}", "muted")))
    console.print(Text(f"  Type a number or any model ID. Press Enter to keep {current}.", style="muted"))
    answer = ui.read()
    if answer.isdigit() and 1 <= int(answer) <= len(suggestions):
        return suggestions[int(answer) - 1][0]
    return answer or None


def _setup_wizard(ui: TerminalUI, provider: str | None = None) -> str | None:
    """Choose a provider, enter and check its key, and pick a model. Returns the provider, or None if abandoned."""
    console.print()
    console.print(Text("  Connect an AI provider", style="bold"))
    console.print(Text("  Sysdoc uses your own API key, so usage is billed to your provider account.", style="muted"))
    if provider is None:
        console.print()
        provider = _pick_provider(ui, config.get_provider())
    info = PROVIDERS[provider]

    variable = config.env_key_name(provider)
    saved = config.get_api_key(provider)
    if variable:
        console.print(Text(f"  Using the {info.label} key from the {variable} environment variable.", style="muted"))
        key, entered = saved, False
    else:
        console.print(Text.assemble("\n  Create a key at ", (info.key_url, "accent")))
        hint = " (hidden; press Enter to keep the saved key)" if saved else " (hidden)"
        key = ""
        while not key:
            key = console.input(f"  Paste your {info.label} API key{hint}: ", password=True).strip() or (saved or "")
        entered = key != saved

    model = config.get_model(provider)
    try:
        with console.status("Checking the key…", spinner="dots"):
            available = create_provider(provider, key, model).list_models()
        ui.notice(f"Connected to {info.label}.", "success")
    except ProviderError as exc:
        ui.notice(str(exc), "error")
        if not typer.confirm("  Save these settings anyway?", default=False):
            return None
        available = []

    console.print(Text("\n  Choose a model", style="bold"))
    model = _pick_model(ui, provider, model) or model
    if available and model not in available:
        ui.notice(f"{model} isn't in the list your key can access. Run 'sysdoc models' to see options.", "warning")
    if entered:
        config.set_api_key(provider, key)
    config.set_provider(provider, model)
    ui.notice(f"Sysdoc will use {info.label} · {model}. Settings saved to {config.config_file()}.", "success")
    return provider


# --- Interactive session -------------------------------------------------------

def _connect(ui: TerminalUI, provider_option: str | None, model_option: str | None) -> Provider:
    settings = config.load_config()
    name = resolve_provider_name(provider_option) if provider_option else config.get_provider(settings)
    if name is None or not config.get_api_key(name, settings):
        name = _setup_wizard(ui, name)
        if name is None:
            raise typer.Exit(code=1)
        settings = config.load_config()
    return create_provider(name, config.get_api_key(name, settings), model_option or config.get_model(name, settings))


def _interactive(provider_option: str | None, model_option: str | None, first_message: str | None, plan_only: bool) -> None:
    from sysdoc.agent.executor import is_admin
    from sysdoc.agent.session import AgentSession

    ui = TerminalUI(console)
    print_banner(console, __version__)  # Before setup, so a first run opens with Sysdoc's banner.
    try:
        provider = _connect(ui, provider_option, model_option)
    except ProviderError as exc:
        ui.notice(str(exc), "error")
        raise typer.Exit(code=1)
    ui.provider_label = PROVIDERS[provider.name].label
    session = AgentSession(provider, ui, scan_mode=config.get_scan_mode(), dry_run=plan_only,
                           remember_scan_mode=config.set_scan_mode)
    print_welcome(console, provider=ui.provider_label, model=provider.model, scan_mode=session.scan_mode, admin=is_admin())
    if plan_only:
        ui.notice("Plan-only mode: Sysdoc will diagnose and show a plan, but won't run it.")
    if first_message:
        console.print()
        console.print(Text.assemble((f"{ui.symbols.prompt} ", "accent"), first_message))
        _turn(session, ui, first_message)

    last_interrupt = 0.0
    try:
        while True:
            console.print()
            try:
                text = ui.read(indent="")
            except KeyboardInterrupt:
                if time.monotonic() - last_interrupt < 2:
                    break
                last_interrupt = time.monotonic()
                console.print()
                ui.notice("Press ctrl+c again to quit, or type /exit.")
                continue
            except EOFError:
                break
            if not text:
                continue
            if text.startswith("/"):
                if not _slash_command(text, session, ui):
                    break
                continue
            _turn(session, ui, text)
        console.print(Text("\n  Bye! Run sysdoc any time something acts up.", style="muted"))
    except KeyboardInterrupt:
        pass  # A second ctrl+c while exiting; leave quietly.


def _turn(session, ui: TerminalUI, text: str) -> None:
    try:
        session.send(text)
    except ProviderError as exc:
        ui.notice(str(exc), "error")
    except KeyboardInterrupt:
        console.print()
        ui.notice("Interrupted. Tell Sysdoc what to do next.", "warning")


def _slash_command(text: str, session, ui: TerminalUI) -> bool:
    """Handle a /command. Returns False when the user wants to quit."""
    command, _, argument = text[1:].partition(" ")
    command, argument = command.lower(), argument.strip()
    if command in ("exit", "quit", "q"):
        return False
    if command in ("help", "?"):
        table = Table.grid(padding=(0, 3))
        table.add_column(style="accent")
        table.add_column()
        for name, description in COMMANDS:
            table.add_row(f"  {name}", description)
        console.print(table)
    elif command in ("new", "clear", "reset"):
        session.reset()
        ui.notice("Started a new conversation.", "success")
    elif command == "scan":
        _run_scan(_all_scanners())
    elif command == "history":
        _print_history(15)
    elif command in ("provider", "model", "setup"):
        _switch(session, ui, command, argument)
    elif command in ("permissions", "permission"):
        console.print(Text("  May Sysdoc scan your PC with read-only checks?", style="bold"))
        choice = ui.choose([("y", "Allow this session"), ("a", "Always allow"), ("e", "Ask me each time"), ("n", "Don't scan")])
        session.scan_mode = {"y": "auto", "a": "auto", "e": "ask", "n": "deny"}[choice]
        config.set_scan_mode("auto" if choice == "a" else None)
        ui.notice("Scan permission updated.", "success")
    else:
        ui.notice(f"Unknown command /{command}. Type /help to see what's available.", "warning")
    return True


def _switch(session, ui: TerminalUI, command: str, argument: str) -> None:
    current: Provider = session.provider
    try:
        if command == "model":
            model = argument or _pick_model(ui, current.name, current.model)
            if not model:
                return
            provider = create_provider(current.name, current.api_key, model)
            config.set_provider(current.name, model)
        else:
            if command == "setup":
                name = _setup_wizard(ui)
            else:
                name = resolve_provider_name(argument) if argument else _pick_provider(ui, current.name)
                if not config.get_api_key(name):
                    name = _setup_wizard(ui, name)
                elif name != current.name:
                    config.set_provider(name)
            if name is None:
                return
            settings = config.load_config()
            provider = create_provider(name, config.get_api_key(name, settings), config.get_model(name, settings))
    except ProviderError as exc:
        ui.notice(str(exc), "error")
        return
    # A different model can't continue another model's conversation, so start fresh.
    session.provider = provider
    session.reset()
    ui.provider_label = PROVIDERS[provider.name].label
    ui.notice(f"Now using {ui.provider_label} · {provider.model}. Started a new conversation.", "success")


def _print_history(limit: int) -> None:
    from sysdoc.agent import audit

    entries = audit.read_history(limit)
    console.print()
    if not entries:
        console.print(Text("  No fixes have been run yet.", style="muted"))
        return
    table = Table(box=None, header_style="muted", padding=(0, 2))
    for column in ("When", "Fix", "Step", "Result"):
        table.add_column(column)
    for entry in entries:
        succeeded = bool(entry.get("succeeded"))
        result = Text("Succeeded" if succeeded else str(entry.get("outcome", "Failed")), style="ok" if succeeded else "err")
        if entry.get("administrator"):
            result.append(" (admin)", style="muted")
        table.add_row(str(entry.get("time", ""))[:16].replace("T", " "), str(entry.get("plan", "")),
                      str(entry.get("step", "")), result)
    console.print(table)
    from sysdoc.agent.audit import history_file
    console.print(Text(f"\n  Full scripts are saved in {history_file()}", style="muted"))


if __name__ == "__main__":
    app()
