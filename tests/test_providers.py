from types import SimpleNamespace

import anthropic
import openai
import pytest
from google.genai import errors, types

from sysdoc.providers import PROVIDERS, create_provider, resolve_provider_name
from sysdoc.providers.anthropic_provider import AnthropicProvider
from sysdoc.providers.base import Message, ProviderError, ToolCall, ToolResult, ToolSpec, friendly_error
from sysdoc.providers.gemini_provider import GeminiProvider
from sysdoc.providers.openai_provider import OpenAIProvider

SPEC = ToolSpec("system_overview", "Snapshot of the PC", {"type": "object", "properties": {}, "required": []})


def conversation():
    return [
        Message("user", text="My game crashes"),
        Message("assistant", text="Checking.", tool_calls=[ToolCall("call-1", "system_overview", {})]),
        Message("tool", tool_results=[ToolResult("call-1", "system_overview", "Windows 11")]),
        Message("user", text="thanks"),
    ]


class Recorder:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def status_error(cls, status, message):
    error = cls.__new__(cls)
    error.status_code, error.body, error.message = status, {"error": {"message": message}}, message
    return error


def test_anthropic_request_and_reply():
    provider = AnthropicProvider("key", "claude-test")
    content = [SimpleNamespace(type="text", text="Found it."),
               SimpleNamespace(type="tool_use", id="toolu_1", name="read_file", input={"path": "C:/x.log"})]
    recorder = Recorder(SimpleNamespace(content=content, stop_reason="tool_use"))
    provider._client = SimpleNamespace(messages=recorder)

    answer = provider.complete("system prompt", conversation(), [SPEC])

    sent = recorder.kwargs
    assert sent["system"] == "system prompt"
    assert sent["tools"] == [{"name": "system_overview", "description": "Snapshot of the PC", "input_schema": SPEC.parameters}]
    assert sent["messages"][1] == {"role": "assistant", "content": [
        {"type": "text", "text": "Checking."}, {"type": "tool_use", "id": "call-1", "name": "system_overview", "input": {}}]}
    assert sent["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call-1", "content": "Windows 11", "is_error": False}]
    assert answer.text == "Found it."
    assert answer.tool_calls[0].arguments == {"path": "C:/x.log"}

    provider.complete("system prompt", [Message("user", text="hi"), answer], [])
    assert recorder.kwargs["messages"][1]["content"] is content  # Replayed exactly, including any signatures.
    assert "tools" not in recorder.kwargs


def test_openai_request_and_reply():
    provider = OpenAIProvider("key", "gpt-test")
    tool_calls = [
        SimpleNamespace(id="c1", type="function", function=SimpleNamespace(name="read_file", arguments='{"path": "C:/x.log"}')),
        SimpleNamespace(id="c2", type="function", function=SimpleNamespace(name="read_file", arguments="{not json")),
    ]
    message = SimpleNamespace(content=None, refusal=None, tool_calls=tool_calls)
    recorder = Recorder(SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")]))
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=recorder))

    answer = provider.complete("system prompt", conversation(), [SPEC])

    sent = recorder.kwargs["messages"]
    assert sent[0] == {"role": "system", "content": "system prompt"}
    assert sent[2]["tool_calls"] == [{"id": "call-1", "type": "function", "function": {"name": "system_overview", "arguments": "{}"}}]
    assert sent[3] == {"role": "tool", "tool_call_id": "call-1", "content": "Windows 11"}
    assert recorder.kwargs["tools"][0]["function"]["parameters"] == SPEC.parameters
    assert answer.tool_calls[0].arguments == {"path": "C:/x.log"}
    assert answer.tool_calls[1].parse_error

    provider.complete("system prompt", [Message("user", text="hi")], [])
    assert "tools" not in recorder.kwargs


def test_gemini_request_and_reply():
    provider = GeminiProvider("key", "gemini-test")
    content = types.Content(role="model", parts=[
        types.Part(text="Checking the log."),
        types.Part(function_call=types.FunctionCall(name="read_file", args={"path": "C:/x.log"})),
    ])
    response = SimpleNamespace(candidates=[SimpleNamespace(content=content, finish_reason=types.FinishReason.STOP)],
                               prompt_feedback=None)
    sent = {}

    def generate_content(**kwargs):
        sent.update(kwargs)
        return response

    provider._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    answer = provider.complete("system prompt", conversation(), [SPEC])

    contents = sent["contents"]
    assert [item.role for item in contents] == ["user", "model", "user"]  # The tool result and next message merge.
    assert contents[1].parts[1].function_call.name == "system_overview"
    assert contents[2].parts[0].function_response.response == {"output": "Windows 11"}
    assert contents[2].parts[1].text == "thanks"
    assert sent["config"].tools[0].function_declarations[0].parameters_json_schema == SPEC.parameters
    assert answer.text == "Checking the log."
    call = answer.tool_calls[0]
    assert call.id.startswith("sysdoc-") and call.arguments == {"path": "C:/x.log"}

    result = Message("tool", tool_results=[ToolResult(call.id, "read_file", "log text", is_error=True)])
    provider.complete("system prompt", [Message("user", text="hi"), answer, result], [])
    assert sent["contents"][1] is content
    response_part = sent["contents"][2].parts[0].function_response
    assert response_part.id is None and response_part.response == {"error": "log text"}
    assert sent["config"].tools is None


def test_sdk_errors_become_friendly_messages():
    claude = AnthropicProvider("key", "claude-test")
    claude._client = SimpleNamespace(messages=Recorder(error=status_error(anthropic.APIStatusError, 401, "invalid x-api-key")))
    with pytest.raises(ProviderError, match="rejected your API key"):
        claude.complete("s", [Message("user", text="hi")], [])

    gpt = OpenAIProvider("key", "gpt-test")
    gpt._client = SimpleNamespace(chat=SimpleNamespace(completions=Recorder(error=status_error(openai.APIStatusError, 404, "no model"))))
    with pytest.raises(ProviderError, match="sysdoc models"):
        gpt.complete("s", [Message("user", text="hi")], [])

    gemini = GeminiProvider("key", "gemini-test")
    error = errors.APIError.__new__(errors.APIError)
    error.code, error.message = 400, "API key not valid. Please pass a valid API key."

    def failing(**kwargs):
        raise error

    gemini._client = SimpleNamespace(models=SimpleNamespace(generate_content=failing))
    with pytest.raises(ProviderError, match="rejected your API key"):
        gemini.complete("s", [Message("user", text="hi")], [])


@pytest.mark.parametrize("status,expected", [
    (None, "Could not reach"), (401, "rejected"), (403, "denied"), (404, "sysdoc models"),
    (429, "rate limiting"), (503, "having problems"), (400, "HTTP 400"),
])
def test_friendly_error_messages(status, expected):
    assert expected in str(friendly_error("Claude", "claude-test", status, "detail"))


def test_registry():
    assert resolve_provider_name("Claude") == "anthropic"
    assert resolve_provider_name("chatgpt") == "openai"
    assert resolve_provider_name("google") == "gemini"
    with pytest.raises(ProviderError):
        resolve_provider_name("llama")
    assert isinstance(create_provider("gpt", "key", "gpt-test"), OpenAIProvider)
    assert set(PROVIDERS) == {"anthropic", "openai", "gemini"}
    for info in PROVIDERS.values():
        assert info.default_model in dict(info.suggested_models)
