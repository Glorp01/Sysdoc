from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".sysdoc"
CONFIG_FILE = CONFIG_DIR / "config.json"


def save_api_key(api_key: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"gemini_api_key": api_key}))


def load_api_key() -> str | None:
    if not CONFIG_FILE.exists():
        return None
    data = json.loads(CONFIG_FILE.read_text())
    return data.get("gemini_api_key")
