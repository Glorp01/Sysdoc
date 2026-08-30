from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="sysdoc",
    help="Diagnose and fix issues with your PC, network, and games.",
    no_args_is_help=True,
)
scan_app = typer.Typer(help="Run diagnostic scans.",)
app.add_typer(scan_app, name="scan")

console = Console()


@scan_app.command("network")
def scan_network() -> None:
    """Check network connectivity, DNS, and latency."""
    console.print("[bold cyan]Running network scan...[/bold cyan]")
    console.print("[yellow]TODO: wire up the network scanner (Step 4)[/yellow]")



@scan_app.command("storage")
def scan_storage() -> None:
    """Check disk space and drive health."""
    console.print("[bold cyan]Running storage scan...[/bold cyan]")
    console.print("[yellow]TODO: wire up the storage scanner (Step 7)[/yellow]")



@scan_app.command("all")
def scan_all() -> None:
    """Run every available scanner."""
    console.print("[bold cyan]Running all scanners...[/bold cyan]")
    console.print("[yellow]TODO: call the orchestrator (step 3)[/yellow]")


@app.command("ask")
def ask(
    question: str = typer.Argument(
        ..., help="A free-form question, e,g. 'why is my ping so high'"
    )
) -> None:
    """Ask the AI assistant a free-form troubleshooting question."""
    console.print(f"[bold cyan]You asked:[/bold cyan] {question}")
    console.print("[yellow]TODO: wire up the AI assistant (Step 5)[/yellow]")


if __name__ == "__main__":
    app()
