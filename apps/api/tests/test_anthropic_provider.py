"""Anthropic provider against a fake HTTP transport (wire format only, no network)."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from brand_api.llm.anthropic_provider import AnthropicProvider, closed_schema, to_anthropic_messages
from brand_api.llm.errors import LLMError
from brand_api.llm.types import (
    ChatRequest,
    Message,
    NativeContent,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolSpec,
)

Handler = Callable[[httpx2.Request], httpx2.Response]
MODEL = "claude-opus-5-5"


def provider(handler: Handler) -> AnthropicProvider:
    return AnthropicProvider(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )


def message(content: list[dict[str, Any]], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 20,
            "output_tokens": 7,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 5,
        },
    }


async def test_chat_request_shape_and_tool_use_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        return httpx2.Response(
            200,
            json=message(
                [
                    {"type": "thinking", "thinking": "", "signature": "sig-1"},
                    {"type": "text", "text": "Ищу в брендбуке."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "search_brandbook",
                        "input": {"query": "тон"},
                    },
                ],
                stop="tool_use",
            ),
        )

    tool = ToolSpec(name="search_brandbook", description="d", parameters={"type": "object"})
    response = await provider(handler).chat(
        MODEL,
        ChatRequest(
            messages=[
                Message(role="system", content="Ты ассистент."),
                Message(role="user", content="тон?"),
            ],
            tools=[tool],
            temperature=0.3,
        ),
    )

    assert seen["system"] == "Ты ассистент."
    assert "temperature" not in seen  # rejected by current Claude models
    assert "tool_choice" not in seen
    assert seen["tools"] == [
        {"name": "search_brandbook", "description": "d", "input_schema": {"type": "object"}}
    ]
    assert seen["messages"] == [{"role": "user", "content": [{"type": "text", "text": "тон?"}]}]
    assert response.content == "Ищу в брендбуке."
    assert response.tool_calls == [
        ToolCall(id="toolu_1", name="search_brandbook", arguments={"query": "тон"})
    ]
    assert response.usage.input_tokens == 25  # includes cache reads
    assert response.native is not None
    assert [b["type"] for b in response.native.blocks] == ["thinking", "text", "tool_use"]


def test_native_blocks_replayed_only_to_the_same_model() -> None:
    native = NativeContent(
        model=MODEL,
        blocks=[
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
        ],
    )
    assistant = Message(role="assistant", tool_calls=[ToolCall(id="t1", name="x")], native=native)
    history = [Message(role="user", content="q"), assistant]

    same = to_anthropic_messages(MODEL, history)
    other = to_anthropic_messages("claude-haiku-4-5", history)

    assert same[1]["content"][0]["type"] == "thinking"
    assert other[1]["content"] == [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]


def test_parallel_tool_results_share_one_user_turn() -> None:
    history = [
        Message(role="user", content="q"),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="a", name="x"), ToolCall(id="b", name="y")],
        ),
        Message(role="tool", tool_call_id="a", content="ra"),
        Message(role="tool", tool_call_id="b", content="rb"),
    ]
    turns = to_anthropic_messages(MODEL, history)

    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in turns[2]["content"]] == ["a", "b"]


def test_closed_schema_forbids_extra_properties_recursively() -> None:
    schema = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {}}}},
        "$defs": {"Post": {"type": "object", "properties": {}}},
    }
    closed = closed_schema(schema)
    assert closed["additionalProperties"] is False
    assert closed["properties"]["items"]["items"]["additionalProperties"] is False
    assert closed["$defs"]["Post"]["additionalProperties"] is False
    assert "additionalProperties" not in schema  # input untouched


async def test_json_schema_uses_output_config() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        return httpx2.Response(200, json=message([{"type": "text", "text": "{}"}]))

    await provider(handler).chat(
        MODEL,
        ChatRequest(messages=[Message(role="user", content="x")], json_schema={"type": "object"}),
    )
    assert seen["output_config"]["format"] == {
        "type": "json_schema",
        "schema": {"type": "object", "additionalProperties": False},
    }


async def test_refusal_becomes_non_retryable_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message([], stop="refusal"))

    with pytest.raises(LLMError) as exc_info:
        await provider(handler).chat(
            MODEL, ChatRequest(messages=[Message(role="user", content="x")])
        )
    assert (exc_info.value.kind, exc_info.value.retryable) == ("refusal", False)


def sse(*events: tuple[str, dict[str, Any]]) -> bytes:
    return "".join(
        f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n" for name, data in events
    ).encode()


async def test_stream_text_deltas_and_final_message() -> None:
    start = message([])
    start["usage"] = {"input_tokens": 15, "output_tokens": 1}
    start["stop_reason"] = None

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert json.loads(request.content)["stream"] is True
        body = sse(
            ("message_start", {"type": "message_start", "message": start}),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Тон "},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "тёплый"},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 4},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = [
        e
        async for e in provider(handler).stream(
            MODEL, ChatRequest(messages=[Message(role="user", content="тон?")])
        )
    ]

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Тон ", "тёплый"]
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.response.content == "Тон тёплый"
    assert done.response.usage.output_tokens == 4
    assert done.response.finish_reason == "end_turn"


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (429, "rate_limit", True),
        (529, "overloaded", True),
        (401, "auth", False),
        (400, "api_error", False),
    ],
)
async def test_http_errors_map_to_llm_errors(status: int, kind: str, retryable: bool) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status, json={"type": "error", "error": {"type": "x", "message": "nope"}}
        )

    with pytest.raises(LLMError) as exc_info:
        await provider(handler).chat(
            MODEL, ChatRequest(messages=[Message(role="user", content="x")])
        )
    assert (exc_info.value.kind, exc_info.value.retryable) == (kind, retryable)
