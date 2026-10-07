"""Persistence of per-call LLM telemetry (tokens, cost, latency, status) into `llm_calls`."""

import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from brand_api.logging_setup import get_logger
from brand_shared.db.models import LLMCall

log = get_logger(__name__)


@dataclass(frozen=True)
class LLMCallRecord:
    provider: str
    model: str
    tier: str | None
    purpose: str
    status: str
    error_type: str | None
    tokens_in: int
    tokens_out: int
    tokens_estimated: bool
    cost_usd: Decimal
    latency_ms: int
    ttft_ms: int | None
    streamed: bool
    attempt: int
    is_fallback: bool
    user_id: uuid.UUID | None
    trace_id: str | None
    request_id: str | None
    cached: bool = False
    cost_saved_usd: Decimal = Decimal(0)


class CallRecorder(Protocol):
    async def record(self, call: LLMCallRecord) -> None: ...


class DbCallRecorder:
    """Writes one row per call. Telemetry failures are logged, never raised to the user."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def record(self, call: LLMCallRecord) -> None:
        try:
            async with self._sessionmaker() as session:
                session.add(LLMCall(**asdict(call)))
                await session.commit()
        except Exception as exc:
            log.error("llm_call_record_failed", error=repr(exc), model=call.model)


class NullRecorder:
    async def record(self, call: LLMCallRecord) -> None:
        return None
