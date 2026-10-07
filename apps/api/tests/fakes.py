"""Scripted LLM provider: the only thing we fake, because external LLM APIs can't run locally."""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from brand_api.llm.errors import LLMError
from brand_api.llm.types import ChatRequest, ChatResponse, StreamDone, StreamEvent, TextDelta, Usage


@dataclass
class FailMidStream:
    """Emit `text` as a delta, then fail — simulates a dropped connection mid-answer."""

    text: str
    error: LLMError


@dataclass
class Hang:
    """Never answer — exercises the router's timeout."""


ScriptItem = ChatResponse | LLMError | FailMidStream | Hang


def reply(content: str = "ok", *, tokens_in: int = 10, tokens_out: int = 5) -> ChatResponse:
    return ChatResponse(
        content=content,
        usage=Usage(input_tokens=tokens_in, output_tokens=tokens_out),
        finish_reason="stop",
    )


def error(
    kind: str = "rate_limit", *, retryable: bool = True, retry_after: float | None = None
) -> LLMError:
    return LLMError(f"fake {kind}", kind=kind, retryable=retryable, retry_after=retry_after)


@dataclass
class ScriptedProvider:
    name: str
    script: list[ScriptItem] = field(default_factory=list)
    requests: list[tuple[str, ChatRequest]] = field(default_factory=list)

    def _next(self, model: str, request: ChatRequest) -> ScriptItem:
        self.requests.append((model, request))
        if not self.script:
            raise AssertionError(f"{self.name}: unexpected extra call")
        return self.script.pop(0)

    async def chat(self, model: str, request: ChatRequest) -> ChatResponse:
        item = self._next(model, request)
        if isinstance(item, Hang):
            await asyncio.Event().wait()
        if isinstance(item, LLMError):
            raise item
        if isinstance(item, FailMidStream):
            raise item.error
        assert isinstance(item, ChatResponse)
        return item

    async def stream(self, model: str, request: ChatRequest) -> AsyncGenerator[StreamEvent, None]:
        item = self._next(model, request)
        if isinstance(item, LLMError):
            raise item
        if isinstance(item, FailMidStream):
            yield TextDelta(item.text)
            raise item.error
        assert isinstance(item, ChatResponse)
        for word in item.content.split(" "):
            yield TextDelta(word + " ")
        yield StreamDone(item)

    async def aclose(self) -> None:
        return None
