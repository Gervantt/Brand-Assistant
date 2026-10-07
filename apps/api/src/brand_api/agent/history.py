"""Conversation transcript persistence (Postgres). The full sequence — including tool calls and
results — is stored so the next turn has the same context the model had."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from brand_api.llm.types import Message, NativeContent, ToolCall
from brand_shared.db.models import ChatMessage


def to_message(row: ChatMessage) -> Message:
    return Message(
        role=row.role,
        content=row.content,
        tool_calls=[ToolCall.model_validate(tc) for tc in row.tool_calls],
        tool_call_id=row.tool_call_id,
        native=NativeContent.model_validate(row.native) if row.native else None,
    )


async def load_history(
    session: AsyncSession, conversation_id: uuid.UUID, limit: int
) -> list[Message]:
    rows = list(
        (
            await session.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.seq.desc())
                .limit(limit)
            )
        ).all()
    )
    rows.reverse()
    # A window must start at a user turn, never in the middle of a tool exchange.
    while rows and rows[0].role != "user":
        rows.pop(0)
    return [to_message(r) for r in rows]


async def append_messages(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    messages: list[Message],
    *,
    extras: dict[int, dict[str, Any]] | None = None,
) -> list[ChatMessage]:
    """Append in order. `extras[i]` may carry `artifacts`, `tool_name` and `meta` for item i."""
    last = await session.scalar(
        select(func.max(ChatMessage.seq)).where(ChatMessage.conversation_id == conversation_id)
    )
    seq = (last or 0) + 1
    rows: list[ChatMessage] = []
    for index, message in enumerate(messages):
        extra = (extras or {}).get(index, {})
        row = ChatMessage(
            conversation_id=conversation_id,
            seq=seq + index,
            role=message.role,
            content=message.content,
            tool_calls=[tc.model_dump() for tc in message.tool_calls],
            tool_call_id=message.tool_call_id,
            tool_name=extra.get("tool_name"),
            native=message.native.model_dump() if message.native else None,
            artifacts=extra.get("artifacts", []),
            meta=extra.get("meta", {}),
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows
