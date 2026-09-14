import pytest

from sysdoc.agent.plan import MAX_STEPS, PlanError, parse_plan


def plan_data(**overrides):
    data = {
        "title": "Rebuild the shader cache",
        "diagnosis": "The game crashes while compiling shaders because its cache is corrupted.",
        "evidence": ["Application Error 1000 for game.exe", "d3d12 cache files dated during the crash"],
        "steps": [
            {"title": "Clear the cache", "explanation": "Deletes cached shaders.", "script": "Write-Output 'clearing'",
             "requires_admin": False, "risk": "low"},
            {"title": "Restart the game", "explanation": "Launch the game again.", "requires_admin": False, "risk": "low"},
        ],
        "rollback": "The cache rebuilds itself automatically.",
        "restart_required": False,
    }
    data.update(overrides)
    return data


def test_valid_plan():
    plan = parse_plan(plan_data())
    assert plan.title == "Rebuild the shader cache"
    assert len(plan.steps) == 2
    assert plan.steps[0].script == "Write-Output 'clearing'"
    assert plan.steps[1].manual
    assert not plan.needs_admin
    assert plan.evidence[0].startswith("Application Error")


def test_admin_steps_and_string_booleans_are_understood():
    steps = [{"title": "Repair", "explanation": "Runs sfc.", "script": "sfc /scannow", "requires_admin": "true", "risk": "Medium"}]
    plan = parse_plan(plan_data(steps=steps, restart_required="false"))
    assert plan.needs_admin
    assert plan.steps[0].risk == "medium"
    assert plan.restart_required is False


@pytest.mark.parametrize("overrides,message", [
    ({"steps": []}, "steps"),
    ({"steps": "run everything"}, "steps"),
    ({"title": "  "}, "title"),
    ({"diagnosis": None}, "diagnosis"),
    ({"steps": [{"title": "x", "explanation": "y", "risk": "extreme"}]}, "Step 1"),
    ({"steps": [{"title": "x", "explanation": "y", "requires_admin": "maybe"}]}, "Step 1"),
    ({"steps": [{"title": "", "explanation": "y"}]}, "Step 1"),
    ({"steps": [{"title": "x", "explanation": "y", "script": "a" * 20000}]}, "too long"),
    ({"steps": [{"title": "x", "explanation": "y"}] * (MAX_STEPS + 1)}, "at most"),
    ({"restart_required": 3}, "restart_required"),
])
def test_invalid_plans_explain_the_problem(overrides, message):
    with pytest.raises(PlanError, match=message):
        parse_plan(plan_data(**overrides))


def test_non_object_is_rejected():
    with pytest.raises(PlanError):
        parse_plan(["not", "a", "plan"])
