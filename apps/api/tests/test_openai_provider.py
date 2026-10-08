"""OpenAI-compatible provider against a fake HTTP transport (wire format only, no network)."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest

from brand_api.llm.errors import LLMError, with_cause
from brand_api.llm.openai_compat import OpenAICompatOptions, OpenAICompatProvider
from brand_api.llm.types import (
    ChatRequest,
    KeepAlive,
    Message,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolSpec,
)

Handler = Callable[[httpx2.Request], httpx2.Response]


def provider(handler: Handler, **options: Any) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        "groq",
        api_key="test",
        base_url="https://llm.test/v1",
        options=OpenAICompatOptions(**options),
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def completion(message: dict[str, Any], finish: str = "stop") -> dict[str, Any]:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
    }


SEARCH_TOOL = ToolSpec(
    name="search_brandbook",
    description="Search the brand book",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}},
)


async def test_chat_sends_tools_and_parses_tool_calls() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        return httpx2.Response(
            200,
            json=completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "search_brandbook",
                                "arguments": '{"query": "тон голоса"}',
                            },
                        }
                    ],
                },
                finish="tool_calls",
            ),
        )

    response = await provider(handler).chat(
        "openai/gpt-oss-120b",
        ChatRequest(
            messages=[Message(role="user", content="тон?")],
            tools=[SEARCH_TOOL],
            temperature=0.2,
            max_tokens=300,
        ),
    )

    assert seen["model"] == "openai/gpt-oss-120b"
    assert seen["max_completion_tokens"] == 300
    assert seen["temperature"] == 0.2
    assert seen["tools"][0]["function"]["name"] == "search_brandbook"
    assert response.tool_calls == [
        ToolCall(id="call_1", name="search_brandbook", arguments={"query": "тон голоса"})
    ]
    assert (response.usage.input_tokens, response.usage.output_tokens) == (12, 7)
    assert response.finish_reason == "tool_calls"


async def test_options_control_temperature_and_token_param() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        return httpx2.Response(200, json=completion({"role": "assistant", "content": "ok"}))

    p = provider(handler, send_temperature=False, max_tokens_param="max_tokens")
    await p.chat("m", ChatRequest(messages=[Message(role="user", content="x")], temperature=0.5))

    assert "temperature" not in seen
    assert "max_tokens" in seen
    assert "max_completion_tokens" not in seen


async def test_history_with_tool_results_is_converted() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        return httpx2.Response(200, json=completion({"role": "assistant", "content": "ok"}))

    history = [
        Message(role="system", content="sys"),
        Message(role="user", content="тон?"),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="c1", name="search_brandbook", arguments={"query": "тон"})],
        ),
        Message(role="tool", tool_call_id="c1", content='{"chunks": []}'),
    ]
    await provider(handler).chat("m", ChatRequest(messages=history))

    assistant, tool = seen["messages"][2], seen["messages"][3]
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"query": "тон"}'
    assert tool == {"role": "tool", "tool_call_id": "c1", "content": '{"chunks": []}'}


async def test_invalid_tool_arguments_are_reported_not_raised() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        call = {"id": "c", "type": "function", "function": {"name": "t", "arguments": "{oops"}}
        return httpx2.Response(
            200, json=completion({"role": "assistant", "content": None, "tool_calls": [call]})
        )

    response = await provider(handler).chat("m", ChatRequest(messages=[]))
    assert response.tool_calls[0].parse_error is not None


async def test_json_schema_maps_to_response_format() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        return httpx2.Response(200, json=completion({"role": "assistant", "content": "{}"}))

    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    await provider(handler).chat(
        "m", ChatRequest(messages=[], json_schema=schema, schema_name="Post")
    )
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["response_format"]["json_schema"]["name"] == "Post"
    assert seen["response_format"]["json_schema"]["schema"] == schema


def sse(*chunks: dict[str, Any]) -> bytes:
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks)
    return (body + "data: [DONE]\n\n").encode()


def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


async def test_stream_yields_text_and_assembles_tool_calls() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert json.loads(request.content)["stream_options"] == {"include_usage": True}
        body = sse(
            chunk({"role": "assistant", "content": "Ищу "}),
            chunk({"content": "в брендбуке"}),
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_9",
                            "type": "function",
                            "function": {"name": "search_brandbook", "arguments": '{"que'},
                        }
                    ]
                }
            ),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'ry": "цвета"}'}}]}),
            chunk({}, finish="tool_calls"),
            {
                "id": "c",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "m",
                "choices": [],
                "usage": {"prompt_tokens": 30, "completion_tokens": 9, "total_tokens": 39},
            },
        )
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = [e async for e in provider(handler).stream("m", ChatRequest(messages=[]))]

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Ищу ", "в брендбуке"]
    assert sum(isinstance(e, KeepAlive) for e in events) == 4  # tool deltas, finish, usage
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.response.content == "Ищу в брендбуке"
    assert done.response.tool_calls[0].arguments == {"query": "цвета"}
    assert done.response.usage.output_tokens == 9
    assert not done.response.usage.estimated


async def test_stream_without_usage_estimates_tokens() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = sse(chunk({"content": "Привет"}, finish="stop"))
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = [e async for e in provider(handler).stream("m", ChatRequest(messages=[]))]
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.response.usage.estimated


@pytest.mark.parametrize(
    ("status", "headers", "kind", "retryable", "retry_after"),
    [
        (429, {"retry-after": "2"}, "rate_limit", True, 2.0),
        (401, {}, "auth", False, None),
        (400, {}, "api_error", False, None),
        (503, {}, "api_error", True, None),
    ],
)
async def test_http_errors_map_to_llm_errors(
    status: int, headers: dict[str, str], kind: str, retryable: bool, retry_after: float | None
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"error": {"message": "nope"}}, headers=headers)

    with pytest.raises(LLMError) as exc_info:
        await provider(handler).chat("m", ChatRequest(messages=[]))
    err = exc_info.value
    assert (err.kind, err.retryable, err.retry_after) == (kind, retryable, retry_after)


def test_connection_error_names_cause_without_leaking_header_value() -> None:
    error = openai.APIConnectionError(request=httpx2.Request("POST", "https://llm.test/v1"))
    error.__cause__ = ValueError("Illegal header value b'Bearer gsk_secret\\n'")
    message = with_cause(error)
    assert "ValueError" in message
    assert "whitespace" in message
    assert "gsk_secret" not in message
