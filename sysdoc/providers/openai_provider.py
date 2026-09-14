"""GPT models through the OpenAI Chat Completions API.

Chat Completions also works with OpenAI-compatible servers: set OPENAI_BASE_URL.
"""
from __future__ import annotations

import json
from typing import Any

import openai

from sysdoc.providers.base import MAX_OUTPUT_TOKENS, Message, Provider, ToolCall, ToolSpec, friendly_error

_NON_CHAT_MARKERS = ("audio", "realtime", "tts", "transcribe", "image", "embedding", "moderation", "dall-e", "whisper", "search")


def _detail(exc: openai.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    return exc.message


def _parse_arguments(raw: str | None) -> tuple[dict[str, Any], str | None]:
    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, f"The arguments were not valid JSON ({exc})."
    if not isinstance(arguments, dict):
        return {}, "The arguments must be a JSON object."
    return arguments, None


class OpenAIProvider(Provider):
    name = "openai"
    label = "GPT"

    def __init__(self, api_key: str, model: str) -> None:
        super().__init__(api_key, model)
        self._client = openai.OpenAI(api_key=api_key, max_retries=2, timeout=300.0)

    @staticmethod
    def _to_api(messages: list[Message]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "user":
                converted.append({"role": "user", "content": message.text})
            elif message.role == "assistant":
                if not message.text and not message.tool_calls:
                    continue
                entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                        }
                        for call in message.tool_calls
                    ]
                converted.append(entry)
            else:
                converted.extend(
                    {"role": "tool", "tool_call_id": result.call_id, "content": result.content or "(no output)"}
                    for result in message.tool_results
                )
        return converted

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> Message:
        options = {"tools": [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools
        ]} if tools else {}
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, *self._to_api(messages)],
                max_completion_tokens=MAX_OUTPUT_TOKENS,
                **options,
            )
        except openai.APIStatusError as exc:
            raise friendly_error(self.label, self.model, exc.status_code, _detail(exc)) from exc
        except openai.APIConnectionError as exc:
            raise friendly_error(self.label, self.model, None) from exc

        if not response.choices:
            return Message("assistant", text="GPT returned an empty response.")
        choice = response.choices[0]
        calls = []
        for call in choice.message.tool_calls or []:
            function = getattr(call, "function", None)
            if function is None:
                continue
            arguments, error = _parse_arguments(function.arguments)
            calls.append(ToolCall(call.id, function.name, arguments, error))
        text = choice.message.content or getattr(choice.message, "refusal", None) or ""
        return Message("assistant", text=text, tool_calls=calls, truncated=choice.finish_reason == "length")

    def list_models(self) -> list[str]:
        try:
            models = [model.id for model in self._client.models.list()]
        except openai.APIStatusError as exc:
            raise friendly_error(self.label, self.model, exc.status_code, _detail(exc)) from exc
        except openai.APIConnectionError as exc:
            raise friendly_error(self.label, self.model, None) from exc
        chat = [m for m in models if m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")) and not any(x in m for x in _NON_CHAT_MARKERS)]
        return sorted(chat or models)
