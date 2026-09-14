"""Provider-neutral chat and tool-calling types shared by every AI backend."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Stays below the size at which SDKs require streaming for a single request.
MAX_OUTPUT_TOKENS = 16000


class ProviderError(RuntimeError):
    """An AI request failed; the message is written to be shown to the user."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Set when the model sent arguments that were not a JSON object.
    parse_error: str | None = None


@dataclass
class ToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: str  # "user", "assistant", or "tool"
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # The provider's native reply, replayed as-is so reasoning signatures survive.
    raw: Any = None
    raw_provider: str | None = None
    truncated: bool = False


class Provider(ABC):
    name: str
    label: str

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    @abstractmethod
    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> Message:
        """Send the conversation and return the assistant's next message."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return the model IDs this API key can use."""

    def native(self, message: Message) -> Any:
        """The message's original provider payload, if this provider produced it."""
        if message.raw is not None and message.raw_provider == self.name:
            return message.raw
        return None


def friendly_error(label: str, model: str, status: int | None, detail: str = "") -> ProviderError:
    """Translate an HTTP failure from any provider into an actionable message."""
    detail = " ".join(detail.split())[:300]
    lowered = detail.lower()
    if status is None:
        return ProviderError(f"Could not reach {label}. Check your internet connection and try again.")
    if status == 401 or "api key not valid" in lowered or "invalid api key" in lowered:
        return ProviderError(f"{label} rejected your API key. Run 'sysdoc setup' to enter a new one.")
    if status == 403:
        return ProviderError(f"{label} denied access with this API key. {detail}".strip())
    if status == 404:
        return ProviderError(
            f"The model '{model}' isn't available to your {label} key. "
            "Run 'sysdoc models' to see which models you can use."
        )
    if status == 429:
        return ProviderError(
            f"{label} is rate limiting requests or your account is out of credits. "
            f"Wait a moment, or check your plan and billing. {detail}".strip()
        )
    if status >= 500:
        return ProviderError(f"{label} is having problems right now (HTTP {status}). Please try again shortly.")
    return ProviderError(f"{label} could not process the request (HTTP {status}). {detail}".strip())
