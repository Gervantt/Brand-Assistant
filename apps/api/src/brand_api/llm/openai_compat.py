"""OpenAI Chat Completions provider. Also serves Groq and Ollama, which expose the same API."""

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2
import openai
from openai import AsyncOpenAI

from brand_api.llm.errors import LLMError
from brand_api.llm.types import (
    ChatRequest,
    ChatResponse,
    KeepAlive,
    Message,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    Usage,
    estimate_tokens,
)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass(frozen=True)
class OpenAICompatOptions:
    # OpenAI reasoning-era models reject custom temperature; Groq/Ollama accept it.
    send_temperature: bool = True
    max_tokens_param: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    extra_body: dict[str, Any] = field(default_factory=dict)


class OpenAICompatProvider:
    def __init__(
        self,
        name: str,
        *,
        api_key: str,
        base_url: str | None = None,
        timeout_s: float = 45.0,
        options: OpenAICompatOptions | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.options = options or OpenAICompatOptions()
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_s,
            max_retries=0,  # the router owns retries so attempts are counted and logged once
            http_client=http_client,
        )

    async def aclose(self) -> None:
        await self._client.close()

    def _params(self, model: str, request: ChatRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "messages": [_to_openai_message(m) for m in request.messages],
            self.options.max_tokens_param: request.max_tokens,
        }
        if request.temperature is not None and self.options.send_temperature:
            params["temperature"] = request.temperature
        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        if request.json_schema is not None:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.json_schema,
                    "strict": False,  # best-effort; we validate with Pydantic ourselves
                },
            }
        if self.options.extra_body:
            params["extra_body"] = self.options.extra_body
        return params

    async def chat(self, model: str, request: ChatRequest) -> ChatResponse:
        try:
            completion = await self._client.chat.completions.create(**self._params(model, request))
        except openai.OpenAIError as exc:
            raise _map_error(exc) from exc

        if not completion.choices:
            raise LLMError("empty completion", kind="empty_response", retryable=True)
        choice = completion.choices[0]
        tool_calls = [
            _parse_tool_call(tc.id, tc.function.name, tc.function.arguments)
            for tc in choice.message.tool_calls or []
            if tc.type == "function"
        ]
        content = choice.message.content or ""
        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            usage=_usage(completion.usage, request, content),
            finish_reason=choice.finish_reason,
            provider=self.name,
            model=model,
        )

    async def stream(self, model: str, request: ChatRequest) -> AsyncGenerator[StreamEvent, None]:
        params = self._params(model, request)
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}

        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        raw_usage: Any = None
        try:
            stream = await self._client.chat.completions.create(**params)
            async for chunk in stream:
                raw_usage = chunk.usage or _groq_usage(chunk.model_extra) or raw_usage
                emitted = False
                for choice in chunk.choices:
                    delta = choice.delta
                    if delta.content:
                        text_parts.append(delta.content)
                        emitted = True
                        yield TextDelta(delta.content)
                    for tc in delta.tool_calls or []:
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                if not emitted:  # reasoning / tool-call deltas: alive but nothing to show
                    yield KeepAlive()
        except openai.OpenAIError as exc:
            raise _map_error(exc) from exc

        content = "".join(text_parts)
        yield StreamDone(
            ChatResponse(
                content=content,
                tool_calls=[
                    _parse_tool_call(c["id"], c["name"], c["arguments"])
                    for _, c in sorted(calls.items())
                ],
                usage=_usage(raw_usage, request, content),
                finish_reason=finish_reason,
                provider=self.name,
                model=model,
            )
        )


def _to_openai_message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in message.tool_calls
            ],
        }
    return {"role": message.role, "content": message.content}


def _parse_tool_call(call_id: str, name: str, arguments: str) -> ToolCall:
    try:
        parsed = json.loads(arguments) if arguments.strip() else {}
    except json.JSONDecodeError as exc:
        return ToolCall(id=call_id, name=name, parse_error=f"invalid JSON arguments: {exc}")
    if not isinstance(parsed, dict):
        return ToolCall(id=call_id, name=name, parse_error="arguments must be a JSON object")
    return ToolCall(id=call_id, name=name, arguments=parsed)


def _groq_usage(extra: dict[str, Any] | None) -> Any:
    # Older Groq streams report usage under `x_groq.usage` instead of `usage`.
    x_groq = (extra or {}).get("x_groq") or {}
    return x_groq.get("usage") if isinstance(x_groq, dict) else None


def _usage(raw: Any, request: ChatRequest, output: str) -> Usage:
    if raw is None:
        prompt = " ".join(m.content for m in request.messages)
        return Usage(
            input_tokens=estimate_tokens(prompt),
            output_tokens=estimate_tokens(output),
            estimated=True,
        )
    if isinstance(raw, dict):
        return Usage(
            input_tokens=int(raw.get("prompt_tokens", 0)),
            output_tokens=int(raw.get("completion_tokens", 0)),
        )
    return Usage(input_tokens=raw.prompt_tokens or 0, output_tokens=raw.completion_tokens or 0)


def _retry_after(exc: openai.APIStatusError) -> float | None:
    value = exc.response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _map_error(exc: openai.OpenAIError) -> LLMError:
    if isinstance(exc, openai.APITimeoutError):
        return LLMError(str(exc), kind="timeout", retryable=True)
    if isinstance(exc, openai.APIConnectionError):
        return LLMError(str(exc), kind="connection", retryable=True)
    if isinstance(exc, openai.RateLimitError):
        return LLMError(
            str(exc),
            kind="rate_limit",
            retryable=True,
            retry_after=_retry_after(exc),
            status_code=exc.status_code,
        )
    if isinstance(exc, openai.APIStatusError):
        retryable = exc.status_code >= 500 or exc.status_code in (408, 409)
        kind = {401: "auth", 403: "auth", 404: "not_found"}.get(exc.status_code, "api_error")
        return LLMError(str(exc), kind=kind, retryable=retryable, status_code=exc.status_code)
    return LLMError(str(exc), kind="unknown", retryable=False)
