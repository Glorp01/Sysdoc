"""Claude models through the Anthropic Messages API."""
from __future__ import annotations

from typing import Any

import anthropic

from sysdoc.providers.base import MAX_OUTPUT_TOKENS, Message, Provider, ToolCall, ToolSpec, friendly_error

try:  # Some older models accept fewer tokens without streaming; the SDK refuses larger requests.
    from anthropic._constants import MODEL_NONSTREAMING_TOKENS
except ImportError:
    MODEL_NONSTREAMING_TOKENS = {}


def _detail(exc: anthropic.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        message = body["error"].get("message")
        if isinstance(message, str):
            return message
    return exc.message


class AnthropicProvider(Provider):
    name = "anthropic"
    label = "Claude"

    def __init__(self, api_key: str, model: str) -> None:
        super().__init__(api_key, model)
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=300.0)

    def _to_api(self, messages: list[Message]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "user":
                converted.append({"role": "user", "content": message.text})
            elif message.role == "assistant":
                content = self.native(message)
                if content is None:
                    content = [{"type": "text", "text": message.text}] if message.text else []
                    content += [
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                        for call in message.tool_calls
                    ]
                if content:
                    converted.append({"role": "assistant", "content": content})
            else:
                converted.append({"role": "user", "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.call_id,
                        "content": result.content or "(no output)",
                        "is_error": result.is_error,
                    }
                    for result in message.tool_results
                ]})
        return converted

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> Message:
        options = {"tools": [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]} if tools else {}
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=min(MAX_OUTPUT_TOKENS, MODEL_NONSTREAMING_TOKENS.get(self.model, MAX_OUTPUT_TOKENS)),
                system=system,
                messages=self._to_api(messages),
                **options,
            )
        except anthropic.APIStatusError as exc:
            raise friendly_error(self.label, self.model, exc.status_code, _detail(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise friendly_error(self.label, self.model, None) from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        calls = [
            ToolCall(block.id, block.name, block.input if isinstance(block.input, dict) else {})
            for block in response.content
            if block.type == "tool_use"
        ]
        if response.stop_reason == "refusal" and not text:
            text = "Claude declined to continue with this request."
        return Message(
            role="assistant",
            text=text,
            tool_calls=calls,
            raw=response.content,
            raw_provider=self.name,
            truncated=response.stop_reason in ("max_tokens", "model_context_window_exceeded"),
        )

    def list_models(self) -> list[str]:
        try:
            return sorted(model.id for model in self._client.models.list(limit=100))
        except anthropic.APIStatusError as exc:
            raise friendly_error(self.label, self.model, exc.status_code, _detail(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise friendly_error(self.label, self.model, None) from exc
