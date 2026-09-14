"""The repair conversation: investigate with read-only tools, propose a plan, and run it only after approval."""
from __future__ import annotations

import json
import threading
from contextlib import AbstractContextManager
from typing import Any, Callable, Protocol

from sysdoc.agent import audit
from sysdoc.agent.executor import CommandResult, clip, is_admin, run_script
from sysdoc.agent.plan import FixPlan, PlanError, PlanStep, parse_plan
from sysdoc.agent.prompts import build_system_prompt
from sysdoc.agent.safety import Verdict, classify_command
from sysdoc.agent.tools import ASK_USER, PROPOSE_PLAN, RUN_COMMAND, TOOL_SPECS, TOOLS, ToolOutcome, windows_version
from sysdoc.providers.base import Message, Provider, ToolCall, ToolResult

MAX_ROUNDS = 40
STEP_TIMEOUT = 2 * 60 * 60  # sfc and DISM repairs can take a long time.
STEP_OUTPUT_LIMIT = 4_000


class AgentUI(Protocol):
    """Everything the session needs from the interface; the terminal UI implements it."""

    def thinking(self) -> AbstractContextManager[Any]: ...
    def assistant_message(self, text: str) -> None: ...
    def notice(self, text: str, kind: str = "info") -> None: ...
    def tool_started(self, title: str, detail: str, purpose: str) -> None: ...
    def tool_running(self) -> AbstractContextManager[Any]: ...
    def tool_finished(self, summary: str, ok: bool) -> None: ...
    def scan_permission(self) -> str: ...  # "session", "always", "ask", or "deny"
    def confirm_tool(self, reason: str, allow_always: bool) -> str: ...  # "yes", "always", or "no"
    def ask_user(self, question: str, choices: list[str]) -> str: ...
    def show_plan(self, plan: FixPlan) -> None: ...
    def plan_decision(self, plan: FixPlan) -> tuple[str, str]: ...  # ("all" | "step" | "revise" | "cancel", feedback)
    def confirm_step(self, number: int, total: int, step: PlanStep) -> str: ...  # "run", "skip", or "stop"
    def manual_step(self, number: int, total: int, step: PlanStep) -> str: ...  # "done", "skip", or "stop"
    def step_started(self, number: int, total: int, step: PlanStep, elevated: bool) -> None: ...
    def step_output(self, line: str) -> None: ...
    def step_finished(self, number: int, step: PlanStep, result: CommandResult) -> None: ...
    def after_failure(self, number: int, step: PlanStep, result: CommandResult, remaining: int) -> str: ...  # "continue" or "stop"


class AgentSession:
    def __init__(
        self,
        provider: Provider,
        ui: AgentUI,
        *,
        scan_mode: str | None = None,
        dry_run: bool = False,
        remember_scan_mode: Callable[[str], None] | None = None,
        runner: Callable[..., CommandResult] = run_script,
        max_rounds: int = MAX_ROUNDS,
    ) -> None:
        self.provider = provider
        self.ui = ui
        # None asks on the first scan; "auto" runs read-only tools freely; "ask" confirms each; "deny" refuses.
        self.scan_mode = scan_mode
        self.dry_run = dry_run
        self.messages: list[Message] = []
        self._remember_scan_mode = remember_scan_mode
        self._runner = runner
        self._max_rounds = max_rounds
        self._system_prompt = build_system_prompt(windows_version(), is_admin(), dry_run)

    def reset(self) -> None:
        self.messages.clear()

    def _request(self) -> Message:
        """Ask the provider for the next reply on a worker thread, so ctrl+c works during slow requests."""
        outcome: dict[str, Any] = {}
        history = list(self.messages)

        def work() -> None:
            try:
                outcome["reply"] = self.provider.complete(self._system_prompt, history, TOOL_SPECS)
            except BaseException as exc:  # Re-raised on the calling thread.
                outcome["error"] = exc

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(0.1)
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def send(self, text: str) -> None:
        """Handle one user message, running tools until the model replies without calling any."""
        self.messages.append(Message("user", text=text))
        for round_number in range(self._max_rounds):
            try:
                with self.ui.thinking():
                    reply = self._request()
            except BaseException:
                if round_number == 0:
                    self.messages.pop()  # Nothing happened yet, so the user can simply retry.
                raise
            self.messages.append(reply)
            if reply.text.strip():
                self.ui.assistant_message(reply.text.strip())
            if not reply.tool_calls:
                if reply.truncated:
                    self.ui.notice("The reply was cut off because it was too long.", "warning")
                return
            self._run_tools(reply)
        self.ui.notice("Pausing: the assistant has taken many steps without finishing. Reply to let it continue.", "warning")

    def _run_tools(self, reply: Message) -> None:
        results: list[ToolResult] = []
        try:
            for call in reply.tool_calls:
                result, cancelled = self._dispatch(call, reply.truncated)
                results.append(result)
                if cancelled:
                    raise KeyboardInterrupt
        except KeyboardInterrupt:
            # Every tool call needs a result, or the next request to the provider is rejected.
            answered = {result.call_id for result in results}
            results += [
                ToolResult(call.id, call.name, "Cancelled: the user interrupted Sysdoc.", True)
                for call in reply.tool_calls if call.id not in answered
            ]
            self.messages.append(Message("tool", tool_results=results))
            raise
        self.messages.append(Message("tool", tool_results=results))

    def _dispatch(self, call: ToolCall, truncated: bool) -> tuple[ToolResult, bool]:
        def failed(message: str) -> tuple[ToolResult, bool]:
            return ToolResult(call.id, call.name, message, True), False

        if call.parse_error:
            return failed(f"Error: {call.parse_error} Call the tool again with valid arguments.")
        if truncated:
            return failed("Error: your reply was cut off before this call was complete. "
                          "Try again with less content, such as fewer or shorter plan steps.")
        if call.name == PROPOSE_PLAN:
            return self._propose_plan(call), False
        if call.name == ASK_USER:
            return self._ask_user(call), False
        tool = TOOLS.get(call.name)
        if tool is None:
            return failed(f"Error: there is no tool named {call.name!r}.")

        title = tool.title
        purpose = ""
        if call.name == RUN_COMMAND:
            title = "Command Prompt" if str(call.arguments.get("shell", "")).lower() == "cmd" else "PowerShell"
            purpose = str(call.arguments.get("purpose") or "").strip()
        self.ui.tool_started(title, tool.detail(call.arguments), purpose)
        refusal = self._authorize(call)
        if refusal is not None:
            self.ui.tool_finished(refusal.summary, ok=False)
            return failed(refusal.content)
        with self.ui.tool_running():
            outcome = tool.run(call.arguments)
        self.ui.tool_finished(outcome.summary, ok=not outcome.is_error)
        return ToolResult(call.id, call.name, outcome.content, outcome.is_error), outcome.cancelled

    def _authorize(self, call: ToolCall) -> ToolOutcome | None:
        """Return a refusal, or None when the tool may run."""
        verdict, reason = Verdict.READ_ONLY, ""
        if call.name == RUN_COMMAND:
            shell = "cmd" if str(call.arguments.get("shell", "")).lower() == "cmd" else "powershell"
            classification = classify_command(str(call.arguments.get("command") or ""), shell)
            verdict, reason = classification.verdict, classification.reason
            if verdict is Verdict.SENSITIVE:
                return ToolOutcome(f"Refused: {reason} Sysdoc never accesses credentials or private keys.",
                                   "Blocked: this would access private data", True)
            if verdict is Verdict.CHANGES:
                return ToolOutcome(
                    f"Refused: {reason} Investigation commands must only read. Use a read-only alternative, "
                    "or include this change in propose_plan so the user can review and approve it.",
                    "Blocked: this would change the PC", True)

        if self.scan_mode is None:
            choice = self.ui.scan_permission()
            if choice == "always" and self._remember_scan_mode:
                self._remember_scan_mode("auto")
            self.scan_mode = {"session": "auto", "always": "auto", "ask": "ask"}.get(choice, "deny")
        if self.scan_mode == "deny":
            return ToolOutcome(
                "Refused: the user hasn't allowed Sysdoc to scan this PC. Don't call investigation tools again "
                "unless the user changes this. Help from the conversation instead, or ask the user for details.",
                "Scanning isn't allowed", True)
        if self.scan_mode == "auto" and verdict is not Verdict.UNKNOWN:
            return None

        answer = self.ui.confirm_tool(reason, allow_always=verdict is not Verdict.UNKNOWN)
        if answer == "always":
            self.scan_mode = "auto"
        if answer in ("yes", "always"):
            return None
        return ToolOutcome("Refused: the user declined this action. Try another approach or ask the user.", "Declined", True)

    def _ask_user(self, call: ToolCall) -> ToolResult:
        question = str(call.arguments.get("question") or "").strip()
        if not question:
            return ToolResult(call.id, call.name, "Error: 'question' is required.", True)
        raw_choices = call.arguments.get("choices")
        choices = [str(choice).strip() for choice in raw_choices if str(choice).strip()] if isinstance(raw_choices, list) else []
        answer = self.ui.ask_user(question, choices[:8]).strip()
        return ToolResult(call.id, call.name, answer or "(The user didn't answer.)")

    def _propose_plan(self, call: ToolCall) -> ToolResult:
        def reply(payload: dict[str, Any]) -> ToolResult:
            return ToolResult(call.id, call.name, json.dumps(payload, indent=1))

        try:
            plan = parse_plan(call.arguments)
        except PlanError as exc:
            return ToolResult(call.id, call.name, f"Error: the plan is invalid. {exc} Fix it and call propose_plan again.", True)
        self.ui.show_plan(plan)
        if self.dry_run:
            self.ui.notice("Plan-only mode: nothing was changed.")
            return reply({"decision": "not_run", "instruction": "Plan-only mode, so nothing ran. Briefly wrap up for the user."})

        choice, feedback = self.ui.plan_decision(plan)
        if choice == "revise":
            return reply({"decision": "changes_requested", "feedback": feedback,
                          "instruction": "Nothing ran. Revise the plan to address the feedback, then call propose_plan again."})
        if choice not in ("all", "step"):
            return reply({"decision": "declined", "feedback": feedback or "(none given)",
                          "instruction": "Nothing ran. Don't propose the same plan again; offer alternatives or advice if useful."})
        return reply({"decision": "approved", "steps": self._execute(plan, step_by_step=choice == "step"),
                      "instruction": "Review the results, verify the fix with read-only tools where possible, then summarize for the user."})

    def _execute(self, plan: FixPlan, step_by_step: bool) -> list[dict[str, Any]]:
        administrator = is_admin()
        total = len(plan.steps)
        report: list[dict[str, Any]] = []
        stopped = False
        for number, step in enumerate(plan.steps, 1):
            entry: dict[str, Any] = {"step": number, "title": step.title}
            report.append(entry)
            if stopped:
                entry["status"] = "not_run"
                continue
            if step.manual:
                answer = self.ui.manual_step(number, total, step)
                entry["status"] = {"done": "done_by_user", "skip": "skipped_by_user"}.get(answer, "not_run")
                stopped = answer == "stop"
                continue
            if step_by_step:
                answer = self.ui.confirm_step(number, total, step)
                if answer != "run":
                    entry["status"] = "skipped_by_user" if answer == "skip" else "not_run"
                    stopped = answer == "stop"
                    continue

            self.ui.step_started(number, total, step, elevated=step.requires_admin and not administrator)
            result = self._runner(step.script, "powershell", timeout=STEP_TIMEOUT, on_output=self.ui.step_output,
                                  elevated=step.requires_admin, strict=True)
            self.ui.step_finished(number, step, result)
            audit.record_step(plan.title, step.title, step.script or "", step.requires_admin, result)
            entry.update({
                "status": "succeeded" if result.ok else "failed",
                "exit_code": result.exit_code,
                "output": clip(result.output, STEP_OUTPUT_LIMIT) or "(no output)",
            })
            if not result.ok:
                entry["problem"] = result.describe()
                if result.cancelled:
                    stopped = True
                elif number < total:
                    stopped = self.ui.after_failure(number, step, result, total - number) == "stop"
        return report
