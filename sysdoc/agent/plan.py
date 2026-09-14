"""The fix plan a model proposes, which the user must approve before anything changes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RISKS = ("low", "medium", "high")
MAX_STEPS = 12
MAX_SCRIPT_CHARS = 12000

PLAN_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short name for the fix, e.g. 'Rebuild the corrupted shader cache'."},
        "diagnosis": {
            "type": "string",
            "description": "Plain-language explanation of what is wrong and why, based on what the scan found.",
        },
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The specific findings that support the diagnosis (log entries, settings, versions).",
        },
        "steps": {
            "type": "array",
            "description": "Ordered steps. Scripts run exactly as written, and only after the user approves.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "What the step does, in a few words."},
                    "explanation": {
                        "type": "string",
                        "description": "What this step changes and why, written for a non-technical user.",
                    },
                    "script": {
                        "type": "string",
                        "description": "Complete, non-interactive Windows PowerShell 5.1 script. "
                                       "Omit it for a manual step the user must do themselves.",
                    },
                    "requires_admin": {"type": "boolean", "description": "True if the script needs administrator rights."},
                    "risk": {
                        "type": "string",
                        "enum": list(RISKS),
                        "description": "low: easily undone or cosmetic; medium: changes settings, drivers, or apps; "
                                       "high: could cause data loss or stop Windows from booting.",
                    },
                },
                "required": ["title", "explanation", "requires_admin", "risk"],
            },
        },
        "rollback": {"type": "string", "description": "How to undo the changes if something goes wrong."},
        "restart_required": {"type": "boolean", "description": "True if the PC must restart for the fix to take effect."},
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Anything to know before approving, e.g. 'Save open work' or 'The game will be closed'.",
        },
    },
    "required": ["title", "diagnosis", "evidence", "steps", "rollback", "restart_required"],
}


class PlanError(ValueError):
    """The proposed plan is malformed; the message is sent back to the model to correct."""


@dataclass(frozen=True)
class PlanStep:
    title: str
    explanation: str
    script: str | None = None  # None means the user performs this step by hand.
    requires_admin: bool = False
    risk: str = "low"

    @property
    def manual(self) -> bool:
        return self.script is None


@dataclass(frozen=True)
class FixPlan:
    title: str
    diagnosis: str
    steps: tuple[PlanStep, ...]
    evidence: tuple[str, ...] = ()
    rollback: str = ""
    restart_required: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def needs_admin(self) -> bool:
        return any(step.requires_admin for step in self.steps if not step.manual)


def _text(data: dict, key: str, *, required: bool = False, limit: int = 4000) -> str:
    value = data.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise PlanError(f"'{key}' must be a string.")
    value = value.strip()
    if required and not value:
        raise PlanError(f"'{key}' is required.")
    if len(value) > limit:
        raise PlanError(f"'{key}' is too long (limit {limit} characters).")
    return value


def _texts(data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PlanError(f"'{key}' must be a list of strings.")
    return tuple(item.strip() for item in value if item.strip())[:20]


def _flag(data: dict, key: str) -> bool:
    value = data.get(key, False)
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    if not isinstance(value, bool):
        raise PlanError(f"'{key}' must be true or false.")
    return value


def parse_plan(data: Any) -> FixPlan:
    if not isinstance(data, dict):
        raise PlanError("The plan must be an object.")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanError("'steps' must be a non-empty list.")
    if len(raw_steps) > MAX_STEPS:
        raise PlanError(f"Use at most {MAX_STEPS} steps; combine closely related commands.")

    steps = []
    for number, raw in enumerate(raw_steps, 1):
        try:
            if not isinstance(raw, dict):
                raise PlanError("must be an object.")
            risk = (_text(raw, "risk") or "low").lower()
            if risk not in RISKS:
                raise PlanError(f"'risk' must be one of: {', '.join(RISKS)}.")
            steps.append(PlanStep(
                title=_text(raw, "title", required=True, limit=120),
                explanation=_text(raw, "explanation", required=True),
                script=_text(raw, "script", limit=MAX_SCRIPT_CHARS) or None,
                requires_admin=_flag(raw, "requires_admin"),
                risk=risk,
            ))
        except PlanError as exc:
            raise PlanError(f"Step {number}: {exc}") from None

    return FixPlan(
        title=_text(data, "title", required=True, limit=120),
        diagnosis=_text(data, "diagnosis", required=True),
        steps=tuple(steps),
        evidence=_texts(data, "evidence"),
        rollback=_text(data, "rollback"),
        restart_required=_flag(data, "restart_required"),
        warnings=_texts(data, "warnings"),
    )
