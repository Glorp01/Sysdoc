from __future__ import annotations

import io
import sys

import typer
from rich.console import Console

from sysdoc import __version__
from sysdoc.core.ai import ask_ai
from sysdoc.core.config import save_api_key
from sysdoc.core.models import ScanResult, Severity
from sysdoc.core.orchestrator import Orchestrator
from sysdoc.scanners.base import Scanner
from sysdoc.scanners.network import NetworkScanner
from sysdoc.scanners.storage import StorageScanner

app = typer.Typer(
    name="sysdoc",
    help="Diagnose and fix issues with your PC, network, and games.",
    no_args_is_help=True,
    invoke_without_command=True,
)
scan_app = typer.Typer(help="Run diagnostic scans.")
app.add_typer(scan_app, name="scan")

# Legacy Windows consoles default to cp1252, which cannot encode the ● marker.
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

console = Console()

SEVERITY_COLORS = {
    Severity.OK: "green",
    Severity.INFO: "cyan",
    Severity.WARNING: "yellow",
    Severity.CRITICAL: "bold red",
}


@app.callback()
def main(version: bool = typer.Option(False, "--version", help="Show the installed version.", is_eager=True)) -> None:
    if version:
        console.print(f"sysdoc {__version__}")
        raise typer.Exit()


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
    """Check GitHub for a new version and update an installed Windows app."""
    from sysdoc.core.updater import (
        UpdateError, check_for_update, download_update, installed_directory, launch_installer,
    )
    try:
        console.print(f"Installed version: {__version__}. Checking GitHub...")
        release = check_for_update()
        if release is None:
            console.print("No newer published version is available.")
            return
        console.print(f"Sysdoc {release.version} is available.")
        if check:
            return
        installed_directory()
        if not yes and not typer.confirm("Download and install the update? Sysdoc will close."):
            return
        with console.status("Downloading and verifying update..."):
            installer = download_update(release)
        launch_installer(installer)
        console.print("Installing update. Sysdoc will reopen when installation finishes.")
    except UpdateError as exc:
        console.print(str(exc), style="red", markup=False)
        raise typer.Exit(code=1)


def _all_scanners() -> list[Scanner]:
    return [NetworkScanner(), StorageScanner()]


def _print_results(results: list[ScanResult]) -> None:
    for result in results:
        console.print(f"\n[bold underline]{result.scanner_name} scan[/bold underline]")
        for finding in result.findings:
            color = SEVERITY_COLORS[finding.severity]
            console.print(f"[{color}]\u25cf {finding.title}[/{color}]")
            console.print(f"   {finding.detail}")
            if finding.suggested_fix:
                console.print(f"   [dim]Fix: {finding.suggested_fix}[/dim]")


@scan_app.command("network")
def scan_network() -> None:
    """Check network connectivity, DNS, and latency."""
    orchestrator = Orchestrator([NetworkScanner()])
    _print_results(orchestrator.run_all())


@scan_app.command("storage")
def scan_storage() -> None:
    """Check disk space and drive health."""
    orchestrator = Orchestrator([StorageScanner()])
    _print_results(orchestrator.run_all())


@scan_app.command("all")
def scan_all() -> None:
    """Run every available scanner."""
    orchestrator = Orchestrator(_all_scanners())
    _print_results(orchestrator.run_all())


@app.command("ask")
def ask(
    question: str = typer.Argument(
        ..., help="A free-form question, e.g. 'why is my ping so high'"
    )
) -> None:
    """Ask the AI assistant a free-form troubleshooting question."""
    console.print("[dim]Gathering system info...[/dim]")
    orchestrator = Orchestrator(_all_scanners())
    results = orchestrator.run_all()

    console.print("[dim]Thinking...[/dim]")
    try:
        answer = ask_ai(question, results)
    except Exception as exc:
        console.print(f"[bold red]AI request failed:[/bold red] {exc}")
        raise typer.Exit(code=1)

    console.print(f"\n[bold cyan]sysdoc:[/bold cyan] {answer}")


@app.command("configure")
def configure() -> None:
    """Save your Gemini API key so you don't have to set it every session."""
    api_key = typer.prompt("Enter your Gemini API key", hide_input=True)
    save_api_key(api_key)
    console.print("[green]API key saved.[/green]")


if __name__ == "__main__":
    app()
