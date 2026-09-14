import pytest

from sysdoc.core import config

PROVIDER_VARIABLES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "SYSDOC_PROVIDER")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep every test away from the real ~/.sysdoc folder and from API keys in the environment."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "sysdoc-home")
    for name in PROVIDER_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return config.CONFIG_DIR
