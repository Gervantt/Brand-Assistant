"""Aggregates over `llm_calls` for /admin/metrics: latency, cost, errors, fallbacks, cache."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from brand_shared.db.models import LLMCall


class CallStats(BaseModel):
    calls: int
    errors: int
    error_rate: float
    fallback_rate: float  # share of successful calls served by a fallback model
    cache_hits: int
    cost_usd: Decimal
    cost_saved_usd: Decimal
    tokens_in: int
    tokens_out: int
    avg_latency_ms: float | None
    p95_latency_ms: float | None


class ModelStats(CallStats):
    provider: str
    model: str


class DailyCost(BaseModel):
    day: date
    calls: int
    cost_usd: Decimal
    cost_saved_usd: Decimal


class MetricsReport(BaseModel):
    window_hours: int
    since: datetime
    totals: CallStats
    by_model: list[ModelStats]
    by_purpose: dict[str, int]
    daily: list[DailyCost]


def _aggregates() -> list[ColumnElement[Any]]:
    ok = LLMCall.status == "ok"
    fresh_ok = ok & LLMCall.cached.is_(False)  # cache hits would flatter latency
    return [
        func.count().label("calls"),
        func.count().filter(LLMCall.status == "error").label("errors"),
        func.count().filter(ok).label("ok"),
        func.count().filter(ok & LLMCall.is_fallback).label("fallbacks"),
        func.count().filter(LLMCall.cached).label("cache_hits"),
        func.coalesce(func.sum(LLMCall.cost_usd), 0).label("cost_usd"),
        func.coalesce(func.sum(LLMCall.cost_saved_usd), 0).label("cost_saved_usd"),
        func.coalesce(func.sum(LLMCall.tokens_in), 0).label("tokens_in"),
        func.coalesce(func.sum(LLMCall.tokens_out), 0).label("tokens_out"),
        func.avg(LLMCall.latency_ms).filter(fresh_ok).label("avg_latency"),
        func.percentile_cont(0.95)
        .within_group(LLMCall.latency_ms)
        .filter(fresh_ok)
        .label("p95_latency"),
    ]


def _ms(value: Any) -> float | None:
    return round(float(value), 1) if value is not None else None


def _stats(row: object) -> dict[str, object]:
    r = row._mapping  # type: ignore[attr-defined]
    calls, ok = int(r["calls"]), int(r["ok"])
    return {
        "calls": calls,
        "errors": int(r["errors"]),
        "error_rate": round(int(r["errors"]) / calls, 4) if calls else 0.0,
        "fallback_rate": round(int(r["fallbacks"]) / ok, 4) if ok else 0.0,
        "cache_hits": int(r["cache_hits"]),
        "cost_usd": Decimal(r["cost_usd"]),
        "cost_saved_usd": Decimal(r["cost_saved_usd"]),
        "tokens_in": int(r["tokens_in"]),
        "tokens_out": int(r["tokens_out"]),
        "avg_latency_ms": round(float(r["avg_latency"]), 1)
        if r["avg_latency"] is not None
        else None,
        "p95_latency_ms": round(float(r["p95_latency"]), 1)
        if r["p95_latency"] is not None
        else None,
    }


async def build_report(session: AsyncSession, hours: int) -> MetricsReport:
    since = datetime.now(UTC) - timedelta(hours=hours)
    in_window = LLMCall.created_at >= since

    totals = (await session.execute(select(*_aggregates()).where(in_window))).one()
    per_model = (
        await session.execute(
            select(LLMCall.provider, LLMCall.model, *_aggregates())
            .where(in_window)
            .group_by(LLMCall.provider, LLMCall.model)
            .order_by(func.count().desc())
        )
    ).all()
    purposes = (
        await session.execute(
            select(
                case((LLMCall.purpose.like("sampling:%"), "sampling"), else_=LLMCall.purpose).label(
                    "purpose"
                ),
                func.count(),
            )
            .where(in_window)
            .group_by("purpose")
        )
    ).all()
    day = cast(func.date_trunc("day", LLMCall.created_at), Date).label("day")
    daily = (
        await session.execute(
            select(
                day,
                func.count(),
                func.coalesce(func.sum(LLMCall.cost_usd), 0),
                func.coalesce(func.sum(LLMCall.cost_saved_usd), 0),
            )
            .where(in_window)
            .group_by(day)
            .order_by(day)
        )
    ).all()

    return MetricsReport(
        window_hours=hours,
        since=since,
        totals=CallStats.model_validate(_stats(totals)),
        by_model=[
            ModelStats.model_validate({"provider": r.provider, "model": r.model, **_stats(r)})
            for r in per_model
        ],
        by_purpose={str(p): int(n) for p, n in purposes},
        daily=[
            DailyCost(day=d, calls=int(n), cost_usd=Decimal(c), cost_saved_usd=Decimal(s))
            for d, n, c, s in daily
        ],
    )
