from __future__ import annotations

import io
import sys

import typer
from rich.console import Console

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
