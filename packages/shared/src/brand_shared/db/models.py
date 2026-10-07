"""ORM models shared by the API gateway and the MCP server."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Table,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from brand_shared.db.base import Base
from brand_shared.permissions import Role

user_clients = Table(
    "user_clients",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("client_id", ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True),
)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(16), default=Role.VIEWER.value)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_demo: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    clients: Mapped[list[Client]] = relationship(secondary=user_clients, lazy="selectin")

    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'copywriter', 'manager', 'admin')", name="role_valid"),
    )


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
