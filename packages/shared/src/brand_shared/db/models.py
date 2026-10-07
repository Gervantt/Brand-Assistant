"""ORM models shared by the API gateway and the MCP server."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from brand_shared.db.base import Base


class LLMCall(Base):
    """One provider HTTP call (each retry/fallback attempt is its own row)."""

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    tier: Mapped[str | None] = mapped_column(String(16))
    purpose: Mapped[str] = mapped_column(String(32), default="agent")
    status: Mapped[str] = mapped_column(String(16))  # ok | error
    error_type: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    tokens_estimated: Mapped[bool] = mapped_column(default=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(default=0)
    ttft_ms: Mapped[int | None] = mapped_column()  # time to first token, streaming only
    streamed: Mapped[bool] = mapped_column(default=False)
    attempt: Mapped[int] = mapped_column(default=1)
    is_fallback: Mapped[bool] = mapped_column(default=False)
    cached: Mapped[bool] = mapped_column(default=False)
    cost_saved_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_llm_calls_model_created", "model", "created_at"),)
