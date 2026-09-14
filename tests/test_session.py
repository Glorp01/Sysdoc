import json
from contextlib import nullcontext

import pytest

from sysdoc.agent import audit
from sysdoc.agent import session as session_module
from sysdoc.agent.executor import CommandResult
from sysdoc.agent.session import AgentSession
from sysdoc.agent.tools import Tool, ToolOutcome
from sysdoc.providers.base import Message, Provider, ProviderError, ToolCall

PLAN = {
    "title": "Repair the game install",
    "diagnosis": "A game file is corrupted.",
    "evidence": ["Application Error 1000 in game.exe"],
    "steps": [
        {"title": "Clear the cache", "explanation": "Removes cached files.", "script": "Write-Output 'clear'",
         "requires_admin": False, "risk": "low"},
        {"title": "Repair system files", "explanation": "Runs sfc.", "script": "sfc /scannow",
         "requires_admin": True, "risk": "medium"},
        {"title": "Relaunch the game", "explanation": "Open the game again.", "requires_admin": False, "risk": "low"},
    ],
    "rollback": "Nothing to undo.",
    "restart_required": False,
}


class ScriptedProvider(Provider):
    name = "scripted"
    label = "Scripted"

    def __init__(self, *replies):
        super().__init__("key", "scripted-model")
        self.replies = list(replies)
        self.requests: list[list[Message]] = []

    def complete(self, system, messages, tools):
        self.requests.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def list_models(self):
        return [self.model]

    def last_results(self):
        return {result.name: result for result in self.requests[-1][-1].tool_results}


class FakeUI:
    def __init__(self, scan="session", plan=("all", ""), confirm="yes", answers=(), step="run", failure="continue"):
        self.scan, self.plan, self.confirm, self.step, self.failure = scan, plan, confirm, step, failure
        self.answers = list(answers)
        self.events = []

    def thinking(self):
        return nullcontext()

    def tool_running(self):
        return nullcontext()

    def assistant_message(self, text):
        self.events.append(("assistant", text))

    def notice(self, text, kind="info"):
        self.events.append(("notice", text))

    def tool_started(self, title, detail, purpose):
        self.events.append(("tool", title, detail))

    def tool_finished(self, summary, ok):
        self.events.append(("tool_finished", summary, ok))

    def scan_permission(self):
        self.events.append(("scan_permission",))
        return self.scan

    def confirm_tool(self, reason, allow_always):
        self.events.append(("confirm", reason, allow_always))
        return self.confirm

    def ask_user(self, question, choices):
        self.events.append(("ask", question, choices))
        return self.answers.pop(0)

    def show_plan(self, plan):
        self.events.append(("plan", plan.title))

    def plan_decision(self, plan):
        return self.plan

    def confirm_step(self, number, total, step):
        return self.step

    def manual_step(self, number, total, step):
        self.events.append(("manual", number))
        return "done"

    def step_started(self, number, total, step, elevated):
        self.events.append(("step", number))

    def step_output(self, line):
        self.events.append(("output", line))

    def step_finished(self, number, step, result):
        self.events.append(("step_finished", number, result.ok))

    def after_failure(self, number, step, result, remaining):
        return self.failure


def calls(*items):
    return Message("assistant", tool_calls=[ToolCall(f"call-{n}", name, args) for n, (name, args) in enumerate(items, 1)])


def reply(text):
    return Message("assistant", text=text)


@pytest.fixture
def ran_tools(monkeypatch):
    """Replace real PC inspection with recorders."""
    ran = []
    for name in ("system_overview", "run_command"):
        original = session_module.TOOLS[name]

        def run(args, name=name):
            ran.append((name, args))
            return ToolOutcome(f"{name} output", f"{name} finished")

        monkeypatch.setitem(session_module.TOOLS, name, Tool(original.spec, original.title, run, original.detail))
    return ran


class Runner:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, script, shell="powershell", **options):
        self.calls.append((script, options))
        options["on_output"](f"ran {script}")
        return self.results.pop(0) if self.results else CommandResult(0, f"ran {script}", 0.1)


def make_session(provider, ui, runner=None, **options):
    return AgentSession(provider, ui, runner=runner or Runner(), **options)


def test_investigate_then_fix_runs_steps_only_after_approval(ran_tools):
    provider = ScriptedProvider(calls(("system_overview", {})), calls(("propose_plan", PLAN)), reply("Fixed it."))
    ui = FakeUI()
    runner = Runner()
    make_session(provider, ui, runner).send("My game crashes")

    assert ran_tools == [("system_overview", {})]
    assert [script for script, _ in runner.calls] == ["Write-Output 'clear'", "sfc /scannow"]
    assert all(options["strict"] for _, options in runner.calls)
    assert [options["elevated"] for _, options in runner.calls] == [False, True]
    outcome = json.loads(provider.last_results()["propose_plan"].content)
    assert outcome["decision"] == "approved"
    assert [step["status"] for step in outcome["steps"]] == ["succeeded", "succeeded", "done_by_user"]
    assert ("assistant", "Fixed it.") in ui.events
    assert [entry["step"] for entry in audit.read_history()] == ["Clear the cache", "Repair system files"]


@pytest.mark.parametrize("decision,expected", [(("cancel", ""), "declined"), (("revise", "Don't reinstall"), "changes_requested")])
def test_nothing_runs_unless_the_plan_is_approved(ran_tools, decision, expected):
    provider = ScriptedProvider(calls(("propose_plan", PLAN)), reply("Okay."))
    runner = Runner()
    make_session(provider, FakeUI(plan=decision), runner).send("fix it")
    assert runner.calls == []
    outcome = json.loads(provider.last_results()["propose_plan"].content)
    assert outcome["decision"] == expected
    assert outcome["feedback"] == (decision[1] or "(none given)")


def test_plan_only_mode_shows_the_plan_without_running_it(ran_tools):
    provider = ScriptedProvider(calls(("propose_plan", PLAN)), reply("Here's the plan."))
    runner = Runner()
    ui = FakeUI()
    make_session(provider, ui, runner, dry_run=True).send("fix it")
    assert runner.calls == []
    assert ("plan", "Repair the game install") in ui.events
    assert json.loads(provider.last_results()["propose_plan"].content)["decision"] == "not_run"


def test_step_by_step_can_skip_and_stop(ran_tools):
    provider = ScriptedProvider(calls(("propose_plan", PLAN)), reply("Done."))
    runner = Runner()
    make_session(provider, FakeUI(plan=("step", ""), step="skip"), runner).send("fix it")
    assert runner.calls == []
    steps = json.loads(provider.last_results()["propose_plan"].content)["steps"]
    assert [step["status"] for step in steps] == ["skipped_by_user", "skipped_by_user", "done_by_user"]


def test_a_failed_step_can_stop_the_remaining_steps(ran_tools):
    provider = ScriptedProvider(calls(("propose_plan", PLAN)), reply("That didn't work."))
    runner = Runner(CommandResult(1, "ERROR: access denied", 0.2))
    make_session(provider, FakeUI(failure="stop"), runner).send("fix it")
    assert len(runner.calls) == 1
    steps = json.loads(provider.last_results()["propose_plan"].content)["steps"]
    assert steps[0]["status"] == "failed" and "access denied" in steps[0]["output"]
    assert [step["status"] for step in steps[1:]] == ["not_run", "not_run"]


def test_invalid_plans_go_back_to_the_model(ran_tools):
    provider = ScriptedProvider(calls(("propose_plan", {"title": "Broken"})), reply("Let me fix that."))
    ui = FakeUI()
    make_session(provider, ui).send("fix it")
    result = provider.last_results()["propose_plan"]
    assert result.is_error and "steps" in result.content
    assert not any(event[0] == "plan" for event in ui.events)


def test_commands_that_change_the_pc_are_refused_during_investigation(ran_tools):
    provider = ScriptedProvider(calls(("run_command", {"command": "Remove-Item C:/Games -Recurse", "purpose": "clean"})), reply("OK"))
    ui = FakeUI()
    make_session(provider, ui).send("free up space")
    assert ran_tools == []
    result = provider.last_results()["run_command"]
    assert result.is_error and "propose_plan" in result.content
    assert ("scan_permission",) not in ui.events


def test_unrecognised_commands_need_explicit_approval_even_when_scans_are_allowed(ran_tools):
    provider = ScriptedProvider(calls(("run_command", {"command": "mytool.exe --check", "purpose": "check"})), reply("OK"))
    ui = FakeUI(confirm="no")
    make_session(provider, ui, scan_mode="auto").send("check")
    assert ran_tools == []
    assert ui.events[1][0] == "confirm" and ui.events[1][2] is False
    assert provider.last_results()["run_command"].is_error


def test_scan_permission_is_asked_once_and_can_be_remembered(ran_tools):
    remembered = []
    provider = ScriptedProvider(
        calls(("system_overview", {}), ("run_command", {"command": "Get-Service", "purpose": "services"})), reply("OK"))
    ui = FakeUI(scan="always")
    session = make_session(provider, ui, remember_scan_mode=remembered.append)
    session.send("slow PC")
    assert ui.events.count(("scan_permission",)) == 1
    assert remembered == ["auto"]
    assert [name for name, _ in ran_tools] == ["system_overview", "run_command"]


def test_ask_each_time_mode_confirms_every_tool(ran_tools):
    provider = ScriptedProvider(calls(("system_overview", {})), reply("OK"))
    ui = FakeUI(scan="ask", confirm="no")
    make_session(provider, ui).send("slow PC")
    assert ran_tools == []
    assert any(event[0] == "confirm" for event in ui.events)


def test_denied_scanning_refuses_tools(ran_tools):
    provider = ScriptedProvider(calls(("system_overview", {})), reply("I can still give advice."))
    make_session(provider, FakeUI(scan="deny")).send("slow PC")
    assert ran_tools == []
    assert "hasn't allowed" in provider.last_results()["system_overview"].content


def test_ask_user_passes_the_answer_back(ran_tools):
    provider = ScriptedProvider(calls(("ask_user", {"question": "Which game?", "choices": ["Fortnite", "Valorant"]})), reply("OK"))
    ui = FakeUI(answers=["Valorant"])
    make_session(provider, ui).send("my game crashes")
    assert ("ask", "Which game?", ["Fortnite", "Valorant"]) in ui.events
    assert provider.last_results()["ask_user"].content == "Valorant"


def test_tool_calls_in_a_cut_off_reply_are_rejected(ran_tools):
    truncated = calls(("propose_plan", PLAN))
    truncated.truncated = True
    provider = ScriptedProvider(truncated, reply("Retrying."))
    runner = Runner()
    make_session(provider, FakeUI(), runner).send("fix it")
    assert runner.calls == []
    assert provider.last_results()["propose_plan"].is_error


def test_a_failed_first_request_can_simply_be_retried():
    provider = ScriptedProvider(ProviderError("offline"), reply("Hello again."))
    session = make_session(provider, FakeUI())
    with pytest.raises(ProviderError):
        session.send("hello")
    assert session.messages == []
    session.send("hello")
    assert [message.role for message in session.messages] == ["user", "assistant"]


def test_interrupting_a_tool_still_answers_every_call(monkeypatch):
    def interrupted(args):
        raise KeyboardInterrupt

    original = session_module.TOOLS["system_overview"]
    monkeypatch.setitem(session_module.TOOLS, "system_overview", Tool(original.spec, original.title, interrupted, original.detail))
    provider = ScriptedProvider(calls(("system_overview", {}), ("run_command", {"command": "Get-Service", "purpose": "x"})))
    session = make_session(provider, FakeUI())
    with pytest.raises(KeyboardInterrupt):
        session.send("slow PC")
    results = session.messages[-1].tool_results
    assert [result.call_id for result in results] == ["call-1", "call-2"]
    assert all(result.is_error for result in results)
