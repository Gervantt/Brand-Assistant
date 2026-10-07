"""Create llm_calls table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("tokens_estimated", sa.Boolean(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ttft_ms", sa.Integer(), nullable=True),
        sa.Column("streamed", sa.Boolean(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("is_fallback", sa.Boolean(), nullable=False),
        sa.Column("cached", sa.Boolean(), nullable=False),
        sa.Column("cost_saved_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index(op.f("ix_llm_calls_created_at"), "llm_calls", ["created_at"])
    op.create_index(op.f("ix_llm_calls_user_id"), "llm_calls", ["user_id"])
    op.create_index(op.f("ix_llm_calls_trace_id"), "llm_calls", ["trace_id"])
    op.create_index("ix_llm_calls_model_created", "llm_calls", ["model", "created_at"])


def downgrade() -> None:
    op.drop_table("llm_calls")
