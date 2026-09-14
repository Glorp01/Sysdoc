"""The AI providers Sysdoc can use, and a factory that loads their SDKs on demand."""
from __future__ import annotations

from dataclasses import dataclass

from sysdoc.providers.base import Message, Provider, ProviderError, ToolCall, ToolResult, ToolSpec

__all__ = [
    "PROVIDERS", "ProviderInfo", "create_provider", "resolve_provider_name",
    "Message", "Provider", "ProviderError", "ToolCall", "ToolResult", "ToolSpec",
]


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    label: str
    company: str
    package: str
    env_vars: tuple[str, ...]
    default_model: str
    suggested_models: tuple[tuple[str, str], ...]
    key_url: str


PROVIDERS: dict[str, ProviderInfo] = {
    "anthropic": ProviderInfo(
        name="anthropic",
        label="Claude",
        company="Anthropic",
        package="anthropic",
        env_vars=("ANTHROPIC_API_KEY",),
        default_model="claude-opus-5",
        suggested_models=(
            ("claude-opus-5", "most capable"),
            ("claude-sonnet-5", "faster, lower cost"),
            ("claude-haiku-4-5", "fastest, lowest cost"),
        ),
        key_url="https://platform.claude.com/settings/keys",
    ),
    "openai": ProviderInfo(
        name="openai",
        label="GPT",
        company="OpenAI",
        package="openai",
        env_vars=("OPENAI_API_KEY",),
        default_model="gpt-5.5",
        suggested_models=(
            ("gpt-5.5", "flagship"),
            ("gpt-5.4-mini", "faster, lower cost"),
        ),
        key_url="https://platform.openai.com/api-keys",
    ),
    "gemini": ProviderInfo(
        name="gemini",
        label="Gemini",
        company="Google",
        package="google-genai",
        env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        default_model="gemini-3.5-flash",
        suggested_models=(
            ("gemini-3.5-flash", "fast, low cost"),
            ("gemini-3.1-pro-preview", "pro model (preview)"),
        ),
        key_url="https://aistudio.google.com/apikey",
    ),
}

_ALIASES = {"claude": "anthropic", "gpt": "openai", "chatgpt": "openai", "google": "gemini"}


def resolve_provider_name(name: str) -> str:
    key = name.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in PROVIDERS:
        raise ProviderError(f"Unknown provider '{name}'. Choose claude, gpt, or gemini.")
    return key


def create_provider(name: str, api_key: str, model: str) -> Provider:
    name = resolve_provider_name(name)
    try:
        if name == "anthropic":
            from sysdoc.providers.anthropic_provider import AnthropicProvider as provider_class
        elif name == "openai":
            from sysdoc.providers.openai_provider import OpenAIProvider as provider_class
        else:
            from sysdoc.providers.gemini_provider import GeminiProvider as provider_class
    except ImportError as exc:
        info = PROVIDERS[name]
        raise ProviderError(
            f"The {info.company} SDK isn't installed. Run: python -m pip install {info.package}"
        ) from exc
    return provider_class(api_key, model)
