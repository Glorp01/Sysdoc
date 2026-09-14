"""Settings stored in ~/.sysdoc/config.json: AI provider, models, API keys, and scan permission.

Reading settings never writes the file, so scans and version checks leave it untouched.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from sysdoc.providers import PROVIDERS, ProviderError, resolve_provider_name

CONFIG_DIR = Path.home() / ".sysdoc"
SCAN_MODES = ("ask", "auto")


def config_file() -> Path:
    return CONFIG_DIR / "config.json"


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(config_file().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
        os.replace(temporary, config_file())
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def env_key_name(provider: str) -> str | None:
    """The environment variable currently supplying this provider's key, if any."""
    for name in PROVIDERS[provider].env_vars:
        if _text(os.environ.get(name)):
            return name
    return None


def get_api_key(provider: str, config: dict[str, Any] | None = None) -> str | None:
    provider = resolve_provider_name(provider)
    variable = env_key_name(provider)
    if variable:
        return os.environ[variable].strip()
    config = load_config() if config is None else config
    keys = config.get("api_keys")
    if isinstance(keys, dict) and _text(keys.get(provider)):
        return _text(keys.get(provider))
    if provider == "gemini":
        return _text(config.get("gemini_api_key"))  # Written by Sysdoc 0.2.
    return None


def get_provider(config: dict[str, Any] | None = None) -> str | None:
    """The chosen provider, or the first one with a key available."""
    config = load_config() if config is None else config
    for candidate in (os.environ.get("SYSDOC_PROVIDER"), config.get("provider")):
        if _text(candidate):
            try:
                return resolve_provider_name(candidate)
            except ProviderError:
                continue
    return next((name for name in PROVIDERS if get_api_key(name, config)), None)


def get_model(provider: str, config: dict[str, Any] | None = None) -> str:
    provider = resolve_provider_name(provider)
    config = load_config() if config is None else config
    models = config.get("models")
    if isinstance(models, dict) and _text(models.get(provider)):
        return _text(models.get(provider))
    return PROVIDERS[provider].default_model


def get_scan_mode(config: dict[str, Any] | None = None) -> str | None:
    config = load_config() if config is None else config
    mode = config.get("scan_permission")
    return mode if mode in SCAN_MODES else None


def set_api_key(provider: str, api_key: str) -> None:
    provider = resolve_provider_name(provider)
    config = load_config()
    keys = config.get("api_keys") if isinstance(config.get("api_keys"), dict) else {}
    keys[provider] = api_key.strip()
    config["api_keys"] = keys
    if provider == "gemini":
        config.pop("gemini_api_key", None)
    save_config(config)


def set_provider(provider: str, model: str | None = None) -> None:
    provider = resolve_provider_name(provider)
    config = load_config()
    config["provider"] = provider
    if model:
        models = config.get("models") if isinstance(config.get("models"), dict) else {}
        models[provider] = model.strip()
        config["models"] = models
    save_config(config)


def set_scan_mode(mode: str | None) -> None:
    config = load_config()
    if mode is None:
        config.pop("scan_permission", None)
    elif mode in SCAN_MODES:
        config["scan_permission"] = mode
    else:
        raise ValueError(f"Unknown scan permission {mode!r}.")
    save_config(config)


def mask_key(key: str) -> str:
    return f"{key[:6]}…{key[-4:]}" if len(key) > 14 else "••••"


# Sysdoc 0.2 compatibility.
def save_api_key(api_key: str) -> None:
    set_api_key("gemini", api_key)


def load_api_key() -> str | None:
    return get_api_key("gemini")
