"""A local record of every fix step Sysdoc ran, kept in ~/.sysdoc/history.jsonl."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from sysdoc.agent.executor import CommandResult
from sysdoc.core import config


def history_file() -> Path:
    return config.CONFIG_DIR / "history.jsonl"


def record_step(plan_title: str, step_title: str, script: str, administrator: bool, result: CommandResult) -> None:
    entry = {
        "time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "plan": plan_title,
        "step": step_title,
        "administrator": administrator,
        "succeeded": result.ok,
        "exit_code": result.exit_code,
        "outcome": "Succeeded." if result.ok else result.describe(),
        "duration_seconds": round(result.duration, 1),
        "script": script,
    }
    try:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with history_file().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # History is a convenience; never fail a fix because it can't be written.


def read_history(limit: int = 20) -> list[dict[str, Any]]:
    try:
        lines = history_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines[-limit:]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries
