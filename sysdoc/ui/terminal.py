"""The interactive terminal interface for the repair agent, built on Rich."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from sysdoc.agent.executor import CommandResult
from sysdoc.agent.plan import FixPlan, PlanStep
from sysdoc.core.models import ScanResult
from sysdoc.ui.theme import ACCENT, BANNER_WIDTH, MUTED, badge, banner, symbols_for

COMMAND_PREVIEW_LINES = 8


class _Working:
    """A spinner with a label and, after a few seconds, the elapsed time."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.started = time.monotonic()
        self.spinner = Spinner("dots", style=ACCENT)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        elapsed = int(time.monotonic() - self.started)
        self.spinner.text = Text.assemble(
            (self.label, "muted"),
            (f" {elapsed}s" if elapsed >= 3 else "", "muted"),
            ("   ctrl+c to interrupt", "dim"),
        )
        yield self.spinner


class TerminalUI:
    def __init__(self, console: Console, provider_label: str = "the AI provider") -> None:
        self.console = console
        self.symbols = symbols_for(console)
        self.provider_label = provider_label

    # --- Input -------------------------------------------------------------

    def read(self, indent: str = "  ") -> str:
        return self.console.input(f"{indent}[accent]{self.symbols.prompt}[/] ").strip()

    def choose(self, options: list[tuple[str, str]], default: str | None = None) -> str:
        """Show [K] label choices and return the chosen key."""
        line = Text("  ")
        for key, label in options:
            line.append(f"[{key.upper()}]", style="accent")
            line.append(f" {label}   ")
        self.console.print(line)
        keys = {key for key, _ in options}
        while True:
            answer = self.read().lower()
            if not answer and default:
                return default
            if answer in ("yes", "yeah", "yep", "ok", "okay", "sure") and "y" in keys:
                return "y"
            if answer in ("no", "nope") and "n" in keys:
                return "n"
            for key, label in options:
                if answer == key or (len(answer) > 1 and label.lower().startswith(answer)):
                    return key
            self.console.print(Text(f"  Type {', '.join(key.upper() for key, _ in options)}.", style="muted"))

    @contextmanager
    def _spinner(self, label: str) -> Iterator[None]:
        with Live(_Working(label), console=self.console, refresh_per_second=12, transient=True):
            yield

    # --- Conversation --------------------------------------------------------

    def thinking(self):
        return self._spinner("Thinking…")

    def assistant_message(self, text: str) -> None:
        self.console.print()
        self.console.print(Text.assemble((f"{self.symbols.assistant} ", "violet"), ("Sysdoc", "bold")))
        self.console.print(Padding(Markdown(text, code_theme="monokai"), (0, 0, 0, 2)))

    def notice(self, text: str, kind: str = "info") -> None:
        style = {"warning": "warn", "error": "err", "success": "ok"}.get(kind, "muted")
        mark = {"warning": "!", "error": self.symbols.fail, "success": self.symbols.ok}.get(kind, self.symbols.dot)
        self.console.print(Text.assemble((f"  {mark} ", style), (text, style)))

    def tool_started(self, title: str, detail: str, purpose: str) -> None:
        line = Text.assemble((f"  {self.symbols.bullet} ", "violet"), (title, "bold"))
        is_command = title in ("PowerShell", "Command Prompt")
        if purpose or (detail and not is_command):
            line.append(f"  {purpose if is_command else detail}", style="muted")
        self.console.print(line)
        if is_command and detail:
            lines = detail.splitlines()
            code = "\n".join(lines[:COMMAND_PREVIEW_LINES])
            if len(lines) > COMMAND_PREVIEW_LINES:
                code += f"\n# … {len(lines) - COMMAND_PREVIEW_LINES} more lines"
            lexer = "powershell" if title == "PowerShell" else "batch"
            self.console.print(Padding(Syntax(code, lexer, theme="ansi_dark", word_wrap=True, background_color="default"), (0, 0, 0, 4)))

    def tool_running(self):
        return self._spinner("Running…")

    def tool_finished(self, summary: str, ok: bool) -> None:
        self.console.print(Text.assemble((f"    {self.symbols.branch} ", "muted"), (summary, "muted" if ok else "err")))

    # --- Permissions and questions ------------------------------------------

    def scan_permission(self) -> str:
        body = Text.assemble(
            "Sysdoc needs to look around your PC to find the cause. It only runs ", ("read-only", "bold"),
            " checks (system details, logs, settings) and shows you each one. Nothing is changed unless you approve a fix plan.\n",
            (f"What it finds is sent to {self.provider_label} for analysis.", "muted"),
        )
        self.console.print()
        self.console.print(Panel(body, title=Text(" Permission to scan ", style="bold"), title_align="left",
                                 border_style=ACCENT, padding=(0, 2)))
        choice = self.choose([("y", "Allow this session"), ("a", "Always allow"), ("e", "Ask me each time"), ("n", "Don't scan")])
        return {"y": "session", "a": "always", "e": "ask", "n": "deny"}[choice]

    def confirm_tool(self, reason: str, allow_always: bool) -> str:
        if reason:
            self.console.print(Text(f"    ! {reason}", style="warn"))
        options = [("y", "Allow")] + ([("a", "Allow all read-only checks")] if allow_always else []) + [("n", "Deny")]
        return {"y": "yes", "a": "always", "n": "no"}[self.choose(options)]

    def ask_user(self, question: str, choices: list[str]) -> str:
        self.console.print()
        self.console.print(Text.assemble((f"{self.symbols.assistant} ", "violet"), ("Sysdoc asks", "bold")))
        self.console.print(Padding(Text(question), (0, 0, 0, 2)))
        for number, choice in enumerate(choices, 1):
            self.console.print(Text.assemble((f"   {number} ", "accent"), choice))
        if choices:
            self.console.print(Text("  Type a number or your own answer.", style="muted"))
        answer = self.read()
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        return answer

    # --- Plans ---------------------------------------------------------------

    def _step_header(self, number: int, total: int, step: PlanStep) -> Text:
        header = Text.assemble((f"  Step {number}/{total}  ", "muted"), (step.title, "bold"), "  ")
        badges = [badge("manual")] if step.manual else [badge(step.risk)] + ([badge("admin")] if step.requires_admin else [])
        for item in badges:
            header.append_text(item)
            header.append(" ")
        return header

    def show_plan(self, plan: FixPlan) -> None:
        parts: list = [Text(plan.title, style="bold"), Text(), Text("WHAT'S WRONG", style="bold violet"), Text(plan.diagnosis)]
        if plan.evidence:
            parts += [Text(), Text("EVIDENCE", style="bold violet")]
            parts += [Text.assemble((f"  {self.symbols.dot} ", "muted"), item) for item in plan.evidence]
        if plan.warnings:
            parts.append(Text())
            parts += [Text.assemble(("! ", "warn"), (warning, "warn")) for warning in plan.warnings]
        self.console.print()
        self.console.print(Panel(Group(*parts), title=Text(" FIX PLAN ", style=f"bold black on {ACCENT}"),
                                 title_align="left", border_style=ACCENT, padding=(1, 2)))
        total = len(plan.steps)
        for number, step in enumerate(plan.steps, 1):
            self.console.print()
            self.console.print(self._step_header(number, total, step))
            self.console.print(Padding(Text(step.explanation), (0, 0, 0, 4)))
            if step.script:
                code = Syntax(step.script, "powershell", theme="monokai", word_wrap=True)
                self.console.print(Padding(
                    Panel(code, title=Text("PowerShell · runs exactly as shown", style="muted"), title_align="left",
                          border_style=MUTED, padding=(0, 1)),
                    (0, 0, 0, 4),
                ))
        self.console.print()
        if plan.rollback:
            self.console.print(Text.assemble(("  Undo: ", "bold"), plan.rollback))
        if plan.restart_required:
            self.console.print(Text.assemble(("  Restart: ", "bold"), "your PC needs to restart afterwards for this to take effect."))
        if plan.needs_admin:
            self.console.print(Text("  Steps marked ADMIN will show a Windows permission prompt.", style="muted"))

    def plan_decision(self, plan: FixPlan) -> tuple[str, str]:
        self.console.print()
        self.console.print(Text("  Apply this fix?", style="bold"))
        choice = self.choose([("y", "Run all steps"), ("s", "Step by step"), ("c", "Request changes"), ("n", "Cancel")])
        if choice == "c":
            self.console.print(Text("  What should change?", style="bold"))
            return "revise", self.read()
        if choice == "n":
            return "cancel", ""
        if any(step.risk == "high" and not step.manual for step in plan.steps):
            self.console.print(Text("  This plan includes HIGH RISK steps. Type yes to continue.", style="err"))
            if self.read().lower() != "yes":
                return "cancel", "The user stopped at the high-risk confirmation."
        return ("all" if choice == "y" else "step"), ""

    def confirm_step(self, number: int, total: int, step: PlanStep) -> str:
        self.console.print()
        self.console.print(self._step_header(number, total, step))
        return {"y": "run", "s": "skip", "x": "stop"}[self.choose([("y", "Run this step"), ("s", "Skip"), ("x", "Stop here")])]

    def manual_step(self, number: int, total: int, step: PlanStep) -> str:
        self.console.print()
        self.console.print(self._step_header(number, total, step))
        self.console.print(Padding(Text(step.explanation), (0, 0, 0, 4)))
        return {"d": "done", "s": "skip", "x": "stop"}[self.choose([("d", "Done"), ("s", "Skip"), ("x", "Stop here")])]

    def step_started(self, number: int, total: int, step: PlanStep, elevated: bool) -> None:
        self.console.print()
        self.console.print(Text.assemble((f"  {self.symbols.step} ", "accent"), (f"Step {number}/{total} ", "muted"), (step.title, "bold")))
        if elevated:
            self.console.print(Text("    Windows will ask for administrator permission.", style="warn"))

    def step_output(self, line: str) -> None:
        self.console.print(Text.assemble((f"    {self.symbols.gutter} ", "muted"), line))

    def step_finished(self, number: int, step: PlanStep, result: CommandResult) -> None:
        if result.ok:
            self.console.print(Text(f"    {self.symbols.ok} Done in {result.duration:.1f}s", style="ok"))
        else:
            self.console.print(Text(f"    {self.symbols.fail} Didn't finish: {result.describe()} ({result.duration:.1f}s)", style="err"))

    def after_failure(self, number: int, step: PlanStep, result: CommandResult, remaining: int) -> str:
        noun = "step" if remaining == 1 else "steps"
        self.console.print(Text(f"  Continue with the remaining {remaining} {noun}?", style="bold"))
        return {"c": "continue", "x": "stop"}[self.choose([("c", "Continue"), ("x", "Stop here")])]


# --- Standalone views --------------------------------------------------------

def print_banner(console: Console, version: str) -> None:
    symbols = symbols_for(console)
    console.print()
    if console.width >= BANNER_WIDTH + 4 and symbols.bullet == "●":
        console.print(Padding(banner(), (0, 0, 0, 2)))
    else:
        console.print(Text("  SYSDOC", style="accent"))
    console.print(Text.assemble(("  AI repair assistant for your PC", "bold"), (f"  {symbols.dot}  v{version}", "muted")))


def print_welcome(console: Console, *, provider: str, model: str, scan_mode: str | None, admin: bool) -> None:
    symbols = symbols_for(console)
    console.print()
    scans = {"auto": "allowed", "ask": "ask each time"}.get(scan_mode or "", "ask first")
    status = Text("  ")
    for label, value, style in (("AI", f"{provider} {symbols.dot} {model}", "ok"), ("Scans", scans, "accent"),
                                ("Admin", "yes" if admin else "no", "violet")):
        status.append(f"{symbols.bullet} ", style=style)
        status.append(f"{label}: ", style="muted")
        status.append(f"{value}    ")
    console.print(status)
    console.print()
    console.print(Text.assemble(
        "  Tell me what's wrong, like ", ("\"Fortnite crashes on launch\"", "accent"), " or ",
        ("\"my Wi-Fi keeps dropping\"", "accent"), ".",
    ))
    console.print(Text("  /help for commands  ·  ctrl+c to interrupt  ·  /exit to quit", style="muted"))


def print_scan_results(console: Console, results: list[ScanResult]) -> None:
    for result in results:
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(no_wrap=True)
        table.add_column(ratio=1)
        for finding in result.findings:
            body = Text.assemble((finding.title, "bold"), "\n", finding.detail)
            if finding.suggested_fix:
                body.append(f"\nFix: {finding.suggested_fix}", style="muted")
            table.add_row(badge(finding.severity.value), body)
        console.print(Panel(table, title=Text(f" {result.scanner_name.title()} scan ", style="bold"), title_align="left",
                            border_style=MUTED, padding=(1, 2)))
