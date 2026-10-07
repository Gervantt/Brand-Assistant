"""Gateway side of MCP: one session per agent run, authenticated as the end user via a signed
service token, with a sampling bridge so MCP generation tools use our LLM router."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any

import httpx2
from mcp import Client
from mcp.client import ClientRequestContext
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    INTERNAL_ERROR,
    CreateMessageRequestParams,
    CreateMessageResult,
    ErrorData,
    TextContent,
)
from mcp.types import Tool as McpTool

from brand_api.auth.principal import Principal
from brand_api.llm.errors import AllModelsFailedError
from brand_api.llm.router import CallContext, LLMRouter
from brand_api.llm.types import ChatRequest, Message, Tier
from brand_shared.internal_auth import InternalClaims, bearer, encode_internal_token
from brand_shared.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    data: dict[str, Any] | None
    text: str


class SamplingBridge:
    """Answers the MCP server's `sampling/createMessage` with our router (tiers, fallback,
    cost accounting all apply to tool-internal generations too)."""

    def __init__(self, router: LLMRouter, ctx: CallContext) -> None:
        self.router = router
        self.ctx = ctx
        self.calls = 0

    async def __call__(
        self, context: ClientRequestContext, params: CreateMessageRequestParams
    ) -> CreateMessageResult | ErrorData:
        self.calls += 1
        messages: list[Message] = []
        if params.system_prompt:
            messages.append(Message(role="system", content=params.system_prompt))
        for item in params.messages:
            text = item.content.text if isinstance(item.content, TextContent) else ""
            messages.append(Message(role=item.role, content=text))
        metadata = params.metadata or {}
        hints = (
            [h.name for h in (params.model_preferences.hints or [])]
            if params.model_preferences
            else []
        )
        tier = Tier.SIMPLE if "simple" in hints else Tier.COMPLEX
        request = ChatRequest(
            messages=messages,
            max_tokens=params.max_tokens,
            temperature=params.temperature,
            json_schema=metadata.get("json_schema"),
            schema_name=str(metadata.get("schema_name", "result")),
        )
        purpose = f"sampling:{metadata.get('purpose', 'tool')}"
        try:
            response = await self.router.chat(
                request, tier=tier, ctx=replace(self.ctx, purpose=purpose)
            )
        except AllModelsFailedError as exc:
            return ErrorData(code=INTERNAL_ERROR, message=exc.user_message)
        return CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text=response.content),
            model=f"{response.provider}/{response.model}",
            stop_reason="endTurn",
        )


class McpSession:
    def __init__(self, client: Client, sampling: SamplingBridge) -> None:
        self._client = client
        self.sampling = sampling

    async def list_tools(self) -> list[McpTool]:
        return list((await self._client.list_tools()).tools)

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        result = await self._client.call_tool(name, arguments)
        text = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
        data: dict[str, Any] | None = result.structured_content
        return ToolOutcome(ok=not result.is_error, data=data, text=text)


class McpGateway:
    def __init__(self, url: str, secret: str, router: LLMRouter, *, read_timeout_s: float) -> None:
        self.url = url
        self._secret = secret
        self._router = router
        self._read_timeout_s = read_timeout_s

    @asynccontextmanager
    async def session(
        self, principal: Principal, *, trace_id: str, user_id: uuid.UUID | None = None
    ) -> AsyncIterator[McpSession]:
        token = encode_internal_token(
            InternalClaims(
                user_id=principal.id,
                role=principal.role,
                client_ids=principal.client_ids,
                trace_id=trace_id,
            ),
            secret=self._secret,
        )
        http = httpx2.AsyncClient(
            headers={"Authorization": bearer(token), "X-Trace-Id": trace_id},
            timeout=httpx2.Timeout(self._read_timeout_s, connect=5.0),
        )
        sampling = SamplingBridge(
            self._router, CallContext(user_id=user_id or principal.id, trace_id=trace_id)
        )
        async with http:
            transport = streamable_http_client(self.url, http_client=http)
            async with Client(transport, sampling_callback=sampling) as client:
                yield McpSession(client, sampling)
