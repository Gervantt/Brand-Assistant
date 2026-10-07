"""Metrics, response cache and tracing — against real Postgres/Redis and the real Langfuse SDK
(spans captured by an in-memory OpenTelemetry exporter instead of being sent)."""

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from redis.asyncio import Redis

from brand_api.config import Settings
from brand_api.db import create_engine, create_sessionmaker
from brand_api.llm.cache import ResponseCache
from brand_api.llm.config import LLMSettings
from brand_api.llm.router import CallContext, LLMRouter
from brand_api.llm.types import ChatRequest, Message, StreamDone, TextDelta, Tier
from brand_api.observability.tracing import Tracer
from brand_shared.db.models import LLMCall
from brand_shared.permissions import Role
from tests.conftest import TEST_REDIS_URL, RoleHeaders
from tests.fakes import ScriptedProvider, error, reply
from tests.test_router import ListRecorder, SleepSpy


def ask(text: str) -> ChatRequest:
    return ChatRequest(messages=[Message(role="user", content=text)])


# ---- metrics ------------------------------------------------------------------------------


async def test_metrics_aggregate_llm_calls(
    client: httpx.AsyncClient, as_role: RoleHeaders, settings: Settings
) -> None:
    model = f"metrics-test-{uuid.uuid4().hex[:8]}"
    rows = [
        # (status, latency, cost, is_fallback, cached, saved)
        ("ok", 100, "0.010000", False, False, "0"),
        ("ok", 200, "0.020000", True, False, "0"),
        ("ok", 1000, "0.030000", False, False, "0"),
        ("error", 50, "0", False, False, "0"),
        ("ok", 1, "0", False, True, "0.040000"),
    ]
    engine = create_engine(settings.database_url)
    async with create_sessionmaker(engine)() as session:
        session.add_all(
            LLMCall(
                provider="test",
                model=model,
                tier="simple",
                purpose="agent",
                status=status,
                error_type=None if status == "ok" else "timeout",
                tokens_in=10,
                tokens_out=5,
                tokens_estimated=False,
                cost_usd=Decimal(cost),
                latency_ms=latency,
                ttft_ms=None,
                streamed=False,
                attempt=1,
                is_fallback=fallback,
                cached=cached,
                cost_saved_usd=Decimal(saved),
                user_id=None,
                trace_id=None,
                request_id=None,
            )
            for status, latency, cost, fallback, cached, saved in rows
        )
        await session.commit()
    await engine.dispose()

    response = await client.get("/admin/metrics?hours=1", headers=await as_role(Role.ADMIN))
    assert response.status_code == 200
    report = response.json()
    stats = next(m for m in report["by_model"] if m["model"] == model)
    assert stats["calls"] == 5
    assert stats["errors"] == 1
    assert stats["error_rate"] == 0.2
    assert stats["fallback_rate"] == 0.25  # 1 of 4 successful calls
    assert stats["cache_hits"] == 1
    assert Decimal(stats["cost_usd"]) == Decimal("0.06")
    assert Decimal(stats["cost_saved_usd"]) == Decimal("0.04")
    assert stats["avg_latency_ms"] == pytest.approx(433.3, abs=0.1)  # cache hit excluded
    assert stats["p95_latency_ms"] == pytest.approx(920.0)
    assert report["totals"]["calls"] >= 5
    assert report["daily"]


@pytest.mark.parametrize("role", [Role.VIEWER, Role.COPYWRITER, Role.MANAGER])
async def test_metrics_are_admin_only(
    client: httpx.AsyncClient, as_role: RoleHeaders, role: Role
) -> None:
    response = await client.get("/admin/metrics", headers=await as_role(role))
    assert response.status_code == 403


# ---- cache --------------------------------------------------------------------------------


@pytest.fixture
async def cache() -> ResponseCache:
    return ResponseCache(Redis.from_url(TEST_REDIS_URL, decode_responses=True), ttl_s=60)


async def test_identical_requests_are_served_from_cache(cache: ResponseCache) -> None:
    groq = ScriptedProvider("groq", [reply("закэшировано", tokens_in=2_000_000, tokens_out=0)])
    recorder = ListRecorder()
    router = LLMRouter(
        LLMSettings(provider="groq"), {"groq": groq}, recorder, sleep=SleepSpy(), cache=cache
    )
    question = ask(f"Уникальный вопрос {uuid.uuid4()}")

    first = await router.chat(question, tier=Tier.SIMPLE)
    second = await router.chat(question, tier=Tier.SIMPLE)
    streamed = [e async for e in router.stream(question, tier=Tier.SIMPLE)]

    assert len(groq.requests) == 1  # provider hit once
    assert first.content == second.content == "закэшировано"
    assert isinstance(streamed[0], TextDelta)
    assert isinstance(streamed[-1], StreamDone)
    hits = [c for c in recorder.calls if c.cached]
    assert len(hits) == 2
    assert all(c.cost_usd == 0 for c in hits)
    assert hits[0].cost_saved_usd == Decimal("0.150000")  # 2M input tokens x $0.075/M


async def test_failures_are_not_cached(cache: ResponseCache) -> None:
    groq = ScriptedProvider("groq", [error("auth", retryable=False), reply("со второго раза")])
    router = LLMRouter(LLMSettings(provider="groq"), {"groq": groq}, ListRecorder(), cache=cache)
    question = ask(f"Вопрос {uuid.uuid4()}")
    with pytest.raises(Exception, match="all models failed"):
        await router.chat(question, tier=Tier.SIMPLE)
    assert (await router.chat(question, tier=Tier.SIMPLE)).content == "со второго раза"


# ---- tracing ------------------------------------------------------------------------------


def test_disabled_tracer_is_a_no_op() -> None:
    tracer = Tracer.create(
        public_key=None, secret_key=None, host=None, environment="test", release="0"
    )
    assert not tracer.enabled
    with tracer.observe("anything", as_type="agent", trace_id="0" * 32) as observation:
        observation.update(output="ignored")


async def test_llm_attempts_become_generations_in_the_request_trace() -> None:
    exporter = InMemorySpanExporter()
    client = Langfuse(
        public_key="pk-test",
        secret_key="sk-test",
        host="http://127.0.0.1:9",
        span_exporter=exporter,
    )
    tracer = Tracer(client)
    groq = ScriptedProvider("groq", [error("rate_limit"), reply("ответ")])
    router = LLMRouter(
        LLMSettings(provider="groq"),
        {"groq": groq},
        ListRecorder(),
        sleep=SleepSpy(),
        tracer=tracer,
    )
    trace_id = uuid.uuid4().hex

    with tracer.observe("agent.run", as_type="agent", trace_id=trace_id, input="вопрос"):
        await router.chat(ask("вопрос"), tier=Tier.SIMPLE, ctx=CallContext(trace_id=trace_id))
    client.flush()

    spans = exporter.get_finished_spans()
    by_name: dict[str, list[Any]] = {}
    for span in spans:
        by_name.setdefault(span.name, []).append(span)
    assert len(by_name["llm.agent"]) == 2  # failed attempt + retry
    assert len(by_name["agent.run"]) == 1
    assert {format(s.context.trace_id, "032x") for s in spans} == {trace_id}
    root = by_name["agent.run"][0]
    assert all(s.parent.span_id == root.context.span_id for s in by_name["llm.agent"])
    levels = sorted(
        str(s.attributes.get("langfuse.observation.level")) for s in by_name["llm.agent"]
    )
    assert "ERROR" in levels
    client.shutdown()
