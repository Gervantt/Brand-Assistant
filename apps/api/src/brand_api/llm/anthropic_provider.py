"""Anthropic Messages API provider.

Notes on current models (Opus 5.5 / Sonnet 5.5): sampling params such as `temperature` and
forced `tool_choice` are rejected, so neither is sent; JSON output uses `output_config.format`;
assistant turns (incl. thinking blocks) are replayed verbatim to the same model.
"""

import copy
from collections.abc import AsyncGenerator
from typing import Any

import anthropic
import httpx2
from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage

from brand_api.llm.errors import LLMError, with_cause
from brand_api.llm.types import (
    ChatRequest,
    ChatResponse,
    KeepAlive,
    Message,
    NativeContent,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    Usage,
)


class AnthropicProvider:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_s: float = 45.0,
        http_client: httpx2.AsyncClient | None = None,
        name: str = "anthropic",
    ) -> None:
        self.name = name
        self._client = AsyncAnthropic(
            api_key=api_key, timeout=timeout_s, max_retries=0, http_client=http_client
        )

    async def aclose(self) -> None:
        await self._client.close()

    def _params(self, model: str, request: ChatRequest) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": to_anthropic_messages(model, request.messages),
        }
        if system:
            params["system"] = system
        if request.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in request.tools
            ]
        if request.json_schema is not None:
            params["output_config"] = {
                "format": {"type": "json_schema", "schema": closed_schema(request.json_schema)}
            }
        return params

    async def chat(self, model: str, request: ChatRequest) -> ChatResponse:
        try:
            message = await self._client.messages.create(**self._params(model, request))
        except anthropic.AnthropicError as exc:
            raise _map_error(exc) from exc
        return self._to_response(model, message)

    async def stream(self, model: str, request: ChatRequest) -> AsyncGenerator[StreamEvent, None]:
        try:
            async with self._client.messages.stream(**self._params(model, request)) as stream:
                async for event in stream:
                    if event.type == "text" and event.text:
                        yield TextDelta(event.text)
                    else:  # thinking / tool input deltas: keep the idle timer alive
                        yield KeepAlive()
                final = await stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise _map_error(exc) from exc
        yield StreamDone(self._to_response(model, final))

    def _to_response(self, model: str, message: AnthropicMessage) -> ChatResponse:
        if message.stop_reason == "refusal":
            raise LLMError("model declined the request", kind="refusal", retryable=False)
        text = "".join(block.text for block in message.content if block.type == "text")
        tool_calls = [
            ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
            for block in message.content
            if block.type == "tool_use"
        ]
        usage = message.usage
        input_tokens = (
            usage.input_tokens
            + (usage.cache_creation_input_tokens or 0)
            + (usage.cache_read_input_tokens or 0)
        )
        return ChatResponse(
            content=text,
            tool_calls=tool_calls,
            usage=Usage(input_tokens=input_tokens, output_tokens=usage.output_tokens),
            finish_reason=message.stop_reason,
            native=NativeContent(
                model=model,
                blocks=[b.model_dump(mode="json", exclude_none=True) for b in message.content],
            ),
            provider=self.name,
            model=model,
        )


def to_anthropic_messages(model: str, messages: list[Message]) -> list[dict[str, Any]]:
    """Neutral history -> Anthropic turns. Consecutive same-role turns are merged so that all
    tool results of one step land in a single user message (keeps parallel tool use working)."""
    turns: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        role, blocks = _blocks_for(model, message)
        if not blocks:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": blocks})
    return turns


def _blocks_for(model: str, message: Message) -> tuple[str, list[dict[str, Any]]]:
    if message.role == "tool":
        return "user", [
            {"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.content}
        ]
    if message.role == "user":
        return "user", [{"type": "text", "text": message.content}]
    if message.native is not None and message.native.model == model:
        return "assistant", copy.deepcopy(message.native.blocks)
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    blocks.extend(
        {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
        for tc in message.tool_calls
    )
    return "assistant", blocks


def closed_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured outputs require `additionalProperties: false` on every object schema."""
    result = copy.deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(result)
    return result


def _map_error(exc: anthropic.AnthropicError) -> LLMError:
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError(str(exc), kind="timeout", retryable=True)
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError(with_cause(exc), kind="connection", retryable=True)
    if isinstance(exc, anthropic.RateLimitError):
        header = exc.response.headers.get("retry-after")
        retry_after = float(header) if header and header.replace(".", "", 1).isdigit() else None
        return LLMError(
            str(exc),
            kind="rate_limit",
            retryable=True,
            retry_after=retry_after,
            status_code=exc.status_code,
        )
    if isinstance(exc, anthropic.APIStatusError):
        retryable = exc.status_code >= 500 or exc.status_code in (408, 409)
        kind = {401: "auth", 403: "auth", 404: "not_found", 529: "overloaded"}.get(
            exc.status_code, "api_error"
        )
        return LLMError(str(exc), kind=kind, retryable=retryable, status_code=exc.status_code)
    return LLMError(str(exc), kind="unknown", retryable=False)
