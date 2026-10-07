"""Agent loop: plan -> tool call -> observation -> answer, with a hard step limit.

Every tool call is re-authorised here (the model only ever *saw* permitted tools, but it can
still emit any name — e.g. under prompt injection), then executed on the MCP server, which
checks permissions a third time.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx2
from mcp.shared.exceptions import MCPError as McpError

from brand_api.agent.events import (
    AgentEvent,
    ArtifactEvent,
    CitationsEvent,
    DoneEvent,
    ErrorEvent,
    MetaEvent,
    StatusEvent,
    TokenEvent,
    ToolEndEvent,
    ToolStartEvent,
)
from brand_api.agent.mcp_client import McpGateway, McpSession
from brand_api.agent.prompts import system_prompt
from brand_api.agent.tools import (
    ARTIFACT_KINDS,
    GENERATION_TOOLS,
    STATUS_LABELS,
    for_model,
    model_tool_specs,
)
from brand_api.auth.principal import Principal
from brand_api.config import AgentSettings
from brand_api.llm.errors import AllModelsFailedError, StreamInterruptedError
from brand_api.llm.router import CallContext, LLMRouter
from brand_api.llm.types import ChatRequest, ChatResponse, Message, TextDelta, ToolCall, ToolSpec
from brand_shared.logging_setup import get_logger
from brand_shared.permissions import Tool

log = get_logger(__name__)


@dataclass
class RunState:
    """Everything the run produced; the caller persists it whatever the outcome."""

    new_messages: list[Message] = field(default_factory=list)
    extras: dict[int, dict[str, Any]] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    error: str | None = None

    def add(self, message: Message, **extra: Any) -> None:
        if extra:
            self.extras[len(self.new_messages)] = extra
        self.new_messages.append(message)


@dataclass(frozen=True)
class RunInput:
    principal: Principal
    client_id: uuid.UUID
    client_name: str
    conversation_id: uuid.UUID
    history: list[Message]
    user_text: str
    trace_id: str


class Agent:
    def __init__(self, router: LLMRouter, mcp: McpGateway, settings: AgentSettings) -> None:
        self.router = router
        self.mcp = mcp
        self.settings = settings

    async def run(self, run: RunInput, state: RunState) -> AsyncIterator[AgentEvent]:
        ctx = CallContext(purpose="agent", user_id=run.principal.id, trace_id=run.trace_id)
        tier = await self.router.classify(run.user_text, ctx)
        yield MetaEvent(str(run.conversation_id), run.trace_id, tier.value)
        state.add(Message(role="user", content=run.user_text))
        messages = [
            Message(
                role="system",
                content=system_prompt(
                    client_name=run.client_name, role=run.principal.role, today=date.today()
                ),
            ),
            *run.history,
            Message(role="user", content=run.user_text),
        ]
        try:
            async with self.mcp.session(run.principal, trace_id=run.trace_id) as mcp:
                specs = model_tool_specs(await mcp.list_tools(), run.principal)
                for step in range(1, self.settings.max_steps + 1):
                    state.steps = step
                    yield StatusEvent("Думаю…" if step == 1 else "Анализирую результат…")
                    response: ChatResponse | None = None
                    async for event in self.router.stream(
                        ChatRequest(messages=messages, tools=specs), tier=tier, ctx=ctx
                    ):
                        if isinstance(event, TextDelta):
                            yield TokenEvent(event.text)
                        else:
                            response = event.response
                    if response is None:  # router contract: a stream always ends with StreamDone
                        raise RuntimeError("LLM stream ended without a final response")
                    assistant = response.as_message()
                    messages.append(assistant)
                    if not response.tool_calls:
                        state.add(assistant)
                        yield DoneEvent(steps=step)
                        return
                    state.add(assistant)
                    for call in response.tool_calls:
                        async for tool_event in self._execute(
                            call, run=run, specs=specs, mcp=mcp, messages=messages, state=state
                        ):
                            yield tool_event
                state.error = "max_steps"
                yield ErrorEvent(
                    "Не удалось завершить задачу за отведённое число шагов. "
                    "Попробуйте упростить запрос.",
                    code="max_steps",
                )
        except Exception as exc:  # the MCP client's task group wraps errors in ExceptionGroups
            root = root_cause(exc)
            state.error = type(root).__name__
            if isinstance(root, AllModelsFailedError | StreamInterruptedError):
                log.warning("agent_llm_failed", error=str(root), trace_id=run.trace_id)
                yield ErrorEvent(root.user_message, code="llm_unavailable")
            elif isinstance(root, OSError | httpx2.HTTPError | McpError):
                log.error("agent_mcp_failed", error=repr(root), trace_id=run.trace_id)
                yield ErrorEvent(
                    "Сервис инструментов временно недоступен. Попробуйте позже.",
                    code="tools_unavailable",
                )
            else:
                log.exception("agent_failed", trace_id=run.trace_id)
                yield ErrorEvent(
                    "Внутренняя ошибка ассистента. Мы уже разбираемся.", code="internal"
                )

    async def _execute(
        self,
        call: ToolCall,
        *,
        run: RunInput,
        specs: list[ToolSpec],
        mcp: McpSession,
        messages: list[Message],
        state: RunState,
    ) -> AsyncIterator[AgentEvent]:
        def respond(content: str, **extra: Any) -> Message:
            message = Message(role="tool", tool_call_id=call.id, content=content)
            messages.append(message)
            state.add(message, tool_name=call.name, **extra)
            return message

        offered = {s.name for s in specs}
        if call.name not in offered or not run.principal.can_use_tool(call.name):
            log.warning(
                "tool_call_blocked",
                tool=call.name,
                role=run.principal.role.value,
                user_id=str(run.principal.id),
                trace_id=run.trace_id,
            )
            respond('{"error": "Инструмент недоступен для роли пользователя. Сообщи об этом."}')
            yield ToolEndEvent(call.id, call.name, ok=False, summary="Нет доступа")
            return
        if call.parse_error:
            respond(f'{{"error": "Некорректные аргументы: {call.parse_error}"}}')
            yield ToolEndEvent(call.id, call.name, ok=False, summary="Некорректные аргументы")
            return

        yield ToolStartEvent(call.id, call.name, STATUS_LABELS.get(call.name, call.name))
        arguments = {**call.arguments, "client_id": str(run.client_id)}  # never model-chosen
        timeout = (
            self.settings.generation_timeout_s
            if call.name in GENERATION_TOOLS
            else self.settings.tool_timeout_s
        )
        started = time.perf_counter()
        try:
            async with asyncio.timeout(timeout):
                outcome = await mcp.call(call.name, arguments)
        except TimeoutError:
            respond('{"error": "Инструмент не ответил вовремя."}')
            yield ToolEndEvent(call.id, call.name, ok=False, summary="Превышено время ожидания")
            return
        log.info(
            "tool_executed",
            tool=call.name,
            ok=outcome.ok,
            duration_ms=round((time.perf_counter() - started) * 1000),
            trace_id=run.trace_id,
        )
        if not outcome.ok:
            respond(f'{{"error": {outcome.text!r}}}')
            yield ToolEndEvent(call.id, call.name, ok=False, summary=outcome.text[:200])
            return

        data = outcome.data or {}
        artifact: dict[str, Any] | None = None
        if call.name in ARTIFACT_KINDS:
            artifact = {"kind": ARTIFACT_KINDS[call.name], "data": data}
            state.artifacts.append(artifact)
            yield ArtifactEvent(kind=artifact["kind"], data=data)
        if call.name == Tool.SEARCH_BRANDBOOK:
            yield CitationsEvent(
                items=data.get("hits", []),
                confidence=float(data.get("confidence", 0.0)),
                found=bool(data.get("found", False)),
            )
        respond(
            for_model(call.name, data, max_chars=self.settings.max_tool_result_chars),
            artifacts=[artifact] if artifact else [],
        )
        yield ToolEndEvent(call.id, call.name, ok=True)


def root_cause(exc: BaseException) -> BaseException:
    """Unwrap (possibly nested) exception groups down to the first real error."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc
