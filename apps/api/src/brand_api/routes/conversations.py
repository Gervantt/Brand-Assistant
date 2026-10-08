import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from sqlalchemy import func, select
from sse_starlette import EventSourceResponse

from brand_api.agent.history import append_messages, load_history
from brand_api.agent.orchestrator import RunInput, RunState
from brand_api.auth.deps import CurrentUser, require
from brand_api.auth.principal import Principal
from brand_api.deps import (
    AgentDep,
    AgentLimiterDep,
    BudgetDep,
    RedisDep,
    SessionDep,
    SessionmakerDep,
    SettingsDep,
)
from brand_api.limits.budget import BudgetExhaustedError
from brand_shared.db.models import ChatMessage, Client, Conversation
from brand_shared.logging_setup import get_logger
from brand_shared.permissions import Action

router = APIRouter(prefix="/conversations", tags=["conversations"])
log = get_logger(__name__)
Asker = Annotated[Principal, Depends(require(Action.ASK))]


class ConversationCreate(BaseModel):
    client_id: uuid.UUID
    title: str = Field(default="", max_length=200)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    role: str
    content: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


async def owned_conversation(
    conversation_id: uuid.UUID, principal: Asker, session: SessionDep
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if (
        conversation is None
        or conversation.user_id != principal.id
        or not principal.can_access_client(conversation.client_id)  # access may be revoked
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Диалог не найден")
    return conversation


OwnedConversation = Annotated[Conversation, Depends(owned_conversation)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate, principal: Asker, session: SessionDep
) -> ConversationOut:
    client = await session.get(Client, body.client_id)
    if client is None or not principal.can_access_client(client.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Клиент не найден")
    conversation = Conversation(user_id=principal.id, client_id=client.id, title=body.title)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.get("")
async def list_conversations(
    principal: CurrentUser, session: SessionDep, client_id: uuid.UUID | None = None
) -> list[ConversationOut]:
    query = (
        select(Conversation)
        .where(Conversation.user_id == principal.id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    if client_id is not None:
        query = query.where(Conversation.client_id == client_id)
    rows = (await session.scalars(query)).all()
    return [
        ConversationOut.model_validate(c) for c in rows if principal.can_access_client(c.client_id)
    ]


@router.get("/{conversation_id}")
async def get_conversation(
    conversation: OwnedConversation, session: SessionDep
) -> ConversationDetail:
    """User-facing transcript: one assistant bubble per turn, with the turn's artifacts."""
    rows = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.seq)
        )
    ).all()
    messages: list[MessageOut] = []
    for row in rows:
        if row.role == "user":
            messages.append(MessageOut(role="user", content=row.content, created_at=row.created_at))
            continue
        if not messages or messages[-1].role != "assistant":
            messages.append(MessageOut(role="assistant", content="", created_at=row.created_at))
        bubble = messages[-1]
        if row.role == "assistant":
            bubble.content += row.content
        bubble.artifacts.extend(row.artifacts)
    detail = ConversationOut.model_validate(conversation).model_dump()
    return ConversationDetail(**detail, messages=messages)


@router.get("/{conversation_id}/state")
async def conversation_state(conversation: OwnedConversation, redis: RedisDep) -> dict[str, Any]:
    raw = await redis.get(_state_key(conversation.id))
    return json.loads(raw) if raw else {"status": "idle"}


def _lock_key(conversation_id: uuid.UUID) -> str:
    return f"agent:lock:{conversation_id}"


def _state_key(conversation_id: uuid.UUID) -> str:
    return f"agent:state:{conversation_id}"


async def _release(redis: Redis, key: str, owner: str) -> None:
    if await redis.get(key) == owner:
        await redis.delete(key)


@router.post("/{conversation_id}/messages")
async def send_message(
    body: SendMessage,
    *,
    conversation: OwnedConversation,
    principal: Asker,
    session: SessionDep,
    sessionmaker: SessionmakerDep,
    redis: RedisDep,
    agent: AgentDep,
    settings: SettingsDep,
    limiter: AgentLimiterDep,
    budget: BudgetDep,
) -> EventSourceResponse:
    """Runs the agent and streams its events (SSE): meta, status, token, tool_start,
    tool_end, artifact, citations, confidence, error, done."""
    limit = await limiter.hit(str(principal.id))
    if not limit.allowed:
        minutes = max(1, round(limit.retry_after_s / 60))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Лимит демо: {limit.limit} запросов к ассистенту в час. "
            f"Попробуйте через {minutes} мин.",
            headers={"Retry-After": str(limit.retry_after_s)},
        )
    try:
        await budget.check()
    except BudgetExhaustedError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, exc.user_message) from exc
    trace_id = uuid.uuid4().hex
    lock_key = _lock_key(conversation.id)
    if not await redis.set(lock_key, trace_id, nx=True, ex=settings.agent.lock_ttl_s):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ассистент ещё отвечает на прошлое сообщение")

    client = await session.get(Client, conversation.client_id)
    history = await load_history(session, conversation.id, settings.agent.history_limit)
    run = RunInput(
        principal=principal,
        client_id=conversation.client_id,
        client_name=client.name if client else "",
        conversation_id=conversation.id,
        history=history,
        user_text=body.content,
        trace_id=trace_id,
    )
    conversation_id, title = conversation.id, conversation.title

    async def events() -> AsyncIterator[dict[str, str]]:
        state = RunState()
        started = time.time()
        await redis.set(
            _state_key(conversation_id),
            json.dumps({"status": "running", "trace_id": trace_id, "started_at": started}),
            ex=settings.agent.lock_ttl_s,
        )
        try:
            async for event in agent.run(run, state):
                yield event.sse()
        finally:
            # Persist even if the client disconnected mid-stream (shielded from cancellation).
            with anyio.CancelScope(shield=True):
                async with sessionmaker() as db:
                    await append_messages(
                        db, conversation_id, state.new_messages, extras=state.extras
                    )
                    row = await db.get(Conversation, conversation_id)
                    if row is not None:
                        row.title = title or body.content[:80]
                        row.updated_at = func.now()
                    await db.commit()
                await redis.delete(_state_key(conversation_id))
                await _release(redis, lock_key, trace_id)
                log.info(
                    "agent_run_finished",
                    trace_id=trace_id,
                    steps=state.steps,
                    error=state.error,
                    duration_s=round(time.time() - started, 2),
                )

    return EventSourceResponse(events(), ping=15, headers={"X-Trace-Id": trace_id})
