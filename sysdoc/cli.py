from __future__ import annotations

import typer
from rich.console import Console

from sysdoc.core.models import ScanResult, Severity
from sysdoc.core.orchestrator import Orchestrator
from sysdoc.scanners.network import NetworkScanner

from sysdoc.core.ai import ask_ai

app = typer.Typer(
    name="sysdoc",
    help="Diagnose and fix issues with your PC, network, and games.",
    no_args_is_help=True,
)
scan_app = typer.Typer(help="Run diagnostic scans.")
app.add_typer(scan_app, name="scan")

console = Console()

SEVERITY_COLORS = {
    Severity.OK: "green",
    Severity.INFO: "cyan",
    Severity.WARNING: "yellow",
    Severity.CRITICAL: "bold red",
}


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
    console.print("[bold cyan]Running storage scan...[/bold cyan]")
    console.print("[yellow]TODO: wire up the storage scanner (Step 7)[/yellow]")


@scan_app.command("all")
def scan_all() -> None:
    """Run every available scanner."""
    orchestrator = Orchestrator([NetworkScanner()])
    _print_results(orchestrator.run_all())


@app.command("ask")
def ask(
    question: str = typer.Argument(
        ..., help="A free-form question, e.g. 'why is my ping so high'"
    )
) -> None:
    """Ask the AI assistant a free-form troubleshooting question."""
    console.print("[dim]Gathering system info...[/dim]")
    orchestrator = Orchestrator([NetworkScanner()])
    results = orchestrator.run_all()

    console.print("[dim]Thinking...[/dim]")
    try:
        answer = ask_ai(question, results)
    except Exception as exc:
        console.print(f"[bold red]AI request failed:[/bold red] {exc}")
        raise typer.Exit(code=1)

    console.print(f"\n[bold cyan]sysdoc:[/bold cyan] {answer}")


if __name__ == "__main__":
    app()
