import json

from sysdoc.core import config


def write_config(data, prefix=""):
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.config_file().write_text(prefix + json.dumps(data), encoding="utf-8")


def test_nothing_configured():
    assert config.get_provider() is None
    assert config.get_api_key("claude") is None
    assert config.get_model("gpt") == "gpt-5.5"
    assert config.get_scan_mode() is None


def test_sysdoc_0_2_gemini_key_still_works():
    write_config({"gemini_api_key": "legacy-key"})
    assert config.get_provider() == "gemini"
    assert config.get_api_key("gemini") == "legacy-key"
    assert config.load_api_key() == "legacy-key"


def test_saving_settings_round_trips_and_migrates_the_legacy_key():
    write_config({"gemini_api_key": "legacy-key"})
    config.set_api_key("gemini", "new-key")
    config.set_api_key("claude", "claude-key")
    config.set_provider("claude", "claude-sonnet-5")
    config.set_scan_mode("auto")
    saved = json.loads(config.config_file().read_text(encoding="utf-8"))
    assert "gemini_api_key" not in saved
    assert saved["api_keys"] == {"gemini": "new-key", "anthropic": "claude-key"}
    assert config.get_provider() == "anthropic"
    assert config.get_model("anthropic") == "claude-sonnet-5"
    assert config.get_scan_mode() == "auto"


def test_environment_keys_take_precedence(monkeypatch):
    write_config({"provider": "openai", "api_keys": {"openai": "saved"}})
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert config.get_api_key("openai") == "from-env"
    assert config.env_key_name("openai") == "OPENAI_API_KEY"
    monkeypatch.setenv("SYSDOC_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-env")
    assert config.get_provider() == "gemini"
    assert config.get_api_key("gemini") == "google-env"


def test_a_provider_with_an_environment_key_is_picked_automatically(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert config.get_provider() == "anthropic"


def test_corrupt_or_bom_files_are_tolerated():
    config.CONFIG_DIR.mkdir(parents=True)
    config.config_file().write_text("{not json", encoding="utf-8")
    assert config.load_config() == {}
    write_config({"provider": "gemini"}, prefix="﻿")
    assert config.get_provider() == "gemini"


def test_reading_settings_never_creates_or_changes_the_file():
    config.get_provider()
    config.get_model("gemini")
    assert not config.config_file().exists()
    write_config({"gemini_api_key": "ci-test-placeholder"})
    before = config.config_file().read_bytes()
    config.get_provider()
    config.get_api_key("gemini")
    assert config.config_file().read_bytes() == before


def test_mask_key_hides_the_middle():
    assert config.mask_key("sk-ant-api03-abcdefghijklmnop") == "sk-ant…mnop"
    assert config.mask_key("short") == "••••"
