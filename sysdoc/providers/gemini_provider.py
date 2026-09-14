"""Gemini models through the Google Gen AI SDK."""
from __future__ import annotations

import uuid

from google import genai
from google.genai import errors, types

from sysdoc.providers.base import MAX_OUTPUT_TOKENS, Message, Provider, ProviderError, ToolCall, ToolSpec, friendly_error

# Gemini doesn't always return call IDs; locally generated ones are never sent back.
_LOCAL_ID = "sysdoc-"
_NETWORK_MODULES = {"httpx", "httpx2", "httpcore", "httpcore2", "aiohttp"}


class GeminiProvider(Provider):
    name = "gemini"
    label = "Gemini"

    def __init__(self, api_key: str, model: str) -> None:
        super().__init__(api_key, model)
        self._client = genai.Client(api_key=api_key)

    def _translate(self, exc: Exception) -> ProviderError | None:
        if type(exc).__name__ == "RetryError" and hasattr(exc, "last_attempt"):
            exc = exc.last_attempt.exception() or exc
        if isinstance(exc, errors.APIError):
            return friendly_error(self.label, self.model, exc.code, exc.message or str(exc))
        if isinstance(exc, OSError) or type(exc).__module__.split(".")[0] in _NETWORK_MODULES:
            return friendly_error(self.label, self.model, None)
        return None

    def _to_api(self, messages: list[Message]) -> list[types.Content]:
        contents: list[types.Content] = []
        for message in messages:
            if message.role == "user":
                content = types.Content(role="user", parts=[types.Part(text=message.text)])
            elif message.role == "assistant":
                content = self.native(message)
                if content is None:
                    parts = [types.Part(text=message.text)] if message.text else []
                    parts += [
                        types.Part(function_call=types.FunctionCall(
                            name=call.name,
                            args=call.arguments,
                            id=None if call.id.startswith(_LOCAL_ID) else call.id,
                        ))
                        for call in message.tool_calls
                    ]
                    if not parts:
                        continue
                    content = types.Content(role="model", parts=parts)
            else:
                content = types.Content(role="user", parts=[
                    types.Part(function_response=types.FunctionResponse(
                        id=None if result.call_id.startswith(_LOCAL_ID) else result.call_id,
                        name=result.name,
                        response={"error": result.content} if result.is_error else {"output": result.content},
                    ))
                    for result in message.tool_results
                ])
            # Gemini expects turns to alternate, so merge back-to-back turns from the same side.
            if contents and contents[-1].role == content.role:
                contents[-1] = types.Content(role=content.role, parts=[*(contents[-1].parts or []), *(content.parts or [])])
            else:
                contents.append(content)
        return contents

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> Message:
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
                for t in tools
            ])] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        try:
            response = self._client.models.generate_content(model=self.model, contents=self._to_api(messages), config=config)
        except Exception as exc:
            error = self._translate(exc)
            if error is None:
                raise
            raise error from exc

        candidate = response.candidates[0] if response.candidates else None
        content = candidate.content if candidate else None
        if content is None or not content.parts:
            feedback = response.prompt_feedback
            reason = (feedback.block_reason if feedback and feedback.block_reason else None) or (
                candidate.finish_reason if candidate else None
            )
            return Message("assistant", text=f"Gemini returned no answer (reason: {reason})." if reason else "Gemini returned an empty answer.")

        text = "".join(part.text for part in content.parts if part.text and not part.thought)
        calls = [
            ToolCall(part.function_call.id or f"{_LOCAL_ID}{uuid.uuid4().hex[:12]}", part.function_call.name or "", dict(part.function_call.args or {}))
            for part in content.parts
            if part.function_call
        ]
        return Message(
            role="assistant",
            text=text,
            tool_calls=calls,
            raw=content,
            raw_provider=self.name,
            truncated=candidate.finish_reason == types.FinishReason.MAX_TOKENS,
        )

    def list_models(self) -> list[str]:
        try:
            models = list(self._client.models.list())
        except Exception as exc:
            error = self._translate(exc)
            if error is None:
                raise
            raise error from exc
        return sorted(
            (model.name or "").removeprefix("models/")
            for model in models
            if "generateContent" in (model.supported_actions or []) and model.name
        )
