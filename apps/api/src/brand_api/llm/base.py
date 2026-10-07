from collections.abc import AsyncGenerator
from typing import Protocol

from brand_api.llm.types import ChatRequest, ChatResponse, StreamEvent


class LLMProvider(Protocol):
    """One provider account. Implementations translate neutral types to the vendor SDK.

    Contract: raise `LLMError` for every failure (never vendor exceptions), do not retry
    internally (the router owns retries/fallback), and end every stream with `StreamDone`.
    """

    name: str

    async def chat(self, model: str, request: ChatRequest) -> ChatResponse: ...

    def stream(self, model: str, request: ChatRequest) -> AsyncGenerator[StreamEvent, None]: ...

    async def aclose(self) -> None: ...
