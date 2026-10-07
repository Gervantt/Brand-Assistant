import asyncio
import uuid
from decimal import Decimal

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from brand_api.config import Settings
from brand_api.db import create_engine, create_sessionmaker
from brand_api.llm.config import LLMSettings
from brand_api.llm.errors import (
    AllModelsFailedError,
    NoProviderConfiguredError,
    StreamInterruptedError,
    StructuredOutputError,
)
from brand_api.llm.router import CallContext, LLMRouter
from brand_api.llm.types import ChatRequest, Message, StreamDone, TextDelta, Tier
from brand_api.observability.llm_calls import DbCallRecorder, LLMCallRecord
from brand_shared.db.models import LLMCall
from brand_shared.json_output import parse_json_model
from tests.fakes import FailMidStream, Hang, ScriptedProvider, SlowThinker, error, reply


class ListRecorder:
    def __init__(self) -> None:
        self.calls: list[LLMCallRecord] = []

    async def record(self, call: LLMCallRecord) -> None:
        self.calls.append(call)


class SleepSpy:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def make_router(
    *providers: ScriptedProvider, fallback: list[str] | None = None, **overrides: object
) -> tuple[LLMRouter, ListRecorder, SleepSpy]:
    settings = LLMSettings(
        provider="groq",
        fallback=["anthropic"] if fallback is None else fallback,
        **overrides,
    )
    recorder, sleep = ListRecorder(), SleepSpy()
    router = LLMRouter(settings, {p.name: p for p in providers}, recorder, sleep=sleep)
    return router, recorder, sleep


def ask(text: str = "Какой тон голоса?") -> ChatRequest:
    return ChatRequest(messages=[Message(role="user", content=text)])


# ---- chain ------------------------------------------------------------------------------


def test_chain_skips_providers_without_credentials() -> None:
    router, _, _ = make_router(
        ScriptedProvider("groq"), ScriptedProvider("anthropic"), fallback=["openai", "anthropic"]
    )
    assert [str(r) for r in router.chain(Tier.COMPLEX)] == [
        "groq/openai/gpt-oss-120b",
        "anthropic/claude-opus-5-5",
    ]
    assert str(router.chain(Tier.SIMPLE)[0]) == "groq/openai/gpt-oss-20b"


def test_exact_model_fallback_entries_are_deduplicated() -> None:
    settings = LLMSettings(
        provider="groq", fallback=["groq/qwen/qwen3.8-27b", "groq/openai/gpt-oss-120b"]
    )
    assert [str(r) for r in settings.chain(Tier.COMPLEX)] == [
        "groq/openai/gpt-oss-120b",
        "groq/qwen/qwen3.8-27b",
    ]


def test_unknown_fallback_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a known provider"):
        LLMSettings(provider="groq", fallback=["mistral"])


async def test_no_configured_provider_gives_actionable_error() -> None:
    router, _, _ = make_router()
    with pytest.raises(NoProviderConfiguredError) as exc_info:
        await router.chat(ask(), tier=Tier.SIMPLE)
    assert "GROQ_API_KEY" in exc_info.value.user_message


# ---- retries & fallback -------------------------------------------------------------------


async def test_retryable_error_is_retried_on_same_model() -> None:
    groq = ScriptedProvider("groq", [error("rate_limit"), reply("готово")])
    router, recorder, sleep = make_router(groq, ScriptedProvider("anthropic"))

    response = await router.chat(ask(), tier=Tier.SIMPLE)

    assert response.content == "готово"
    assert response.provider == "groq"
    assert not response.is_fallback
    assert len(sleep.delays) == 1
    assert [(c.status, c.attempt) for c in recorder.calls] == [("error", 1), ("ok", 2)]


async def test_backoff_grows_exponentially() -> None:
    groq = ScriptedProvider("groq", [error(), error(), reply()])
    router, _, sleep = make_router(groq, backoff_initial_s=1.0, backoff_max_s=100.0)
    await router.chat(ask(), tier=Tier.SIMPLE)
    first, second = sleep.delays
    assert 0.5 <= first <= 1.0
    assert 1.0 <= second <= 2.0


async def test_non_retryable_error_falls_back_immediately() -> None:
    groq = ScriptedProvider("groq", [error("auth", retryable=False)])
    anthropic = ScriptedProvider("anthropic", [reply("from claude")])
    router, recorder, sleep = make_router(groq, anthropic)

    response = await router.chat(ask(), tier=Tier.COMPLEX)

    assert (response.provider, response.model) == ("anthropic", "claude-opus-5-5")
    assert response.is_fallback
    assert sleep.delays == []
    assert recorder.calls[-1].is_fallback


async def test_exhausted_retries_fall_back() -> None:
    groq = ScriptedProvider("groq", [error(), error(), error()])
    anthropic = ScriptedProvider("anthropic", [reply()])
    router, recorder, _ = make_router(groq, anthropic, max_retries=2)

    await router.chat(ask(), tier=Tier.SIMPLE)

    assert [c.provider for c in recorder.calls] == ["groq"] * 3 + ["anthropic"]


async def test_long_retry_after_skips_straight_to_fallback() -> None:
    groq = ScriptedProvider("groq", [error("rate_limit", retry_after=60)])
    anthropic = ScriptedProvider("anthropic", [reply()])
    router, _, sleep = make_router(groq, anthropic, backoff_max_s=8)

    response = await router.chat(ask(), tier=Tier.SIMPLE)

    assert response.is_fallback
    assert sleep.delays == []


async def test_all_models_failing_raises_with_attempt_summary() -> None:
    groq = ScriptedProvider("groq", [error("auth", retryable=False)])
    anthropic = ScriptedProvider("anthropic", [error("overloaded", retryable=False)])
    router, _, _ = make_router(groq, anthropic)

    with pytest.raises(AllModelsFailedError) as exc_info:
        await router.chat(ask(), tier=Tier.SIMPLE)

    assert [a.kind for a in exc_info.value.attempts] == ["auth", "overloaded"]
    assert "недоступен" in exc_info.value.user_message


async def test_hanging_call_times_out_and_falls_back() -> None:
    groq = ScriptedProvider("groq", [Hang()])
    anthropic = ScriptedProvider("anthropic", [reply()])
    router, recorder, _ = make_router(groq, anthropic, timeout_s=0.05, max_retries=0)

    response = await asyncio.wait_for(router.chat(ask(), tier=Tier.SIMPLE), timeout=2)

    assert response.is_fallback
    assert recorder.calls[0].error_type == "timeout"


async def test_default_temperature_is_applied() -> None:
    groq = ScriptedProvider("groq", [reply()])
    router, _, _ = make_router(groq, temperature=0.7)
    await router.chat(ask(), tier=Tier.SIMPLE)
    assert groq.requests[0][1].temperature == 0.7


# ---- streaming ------------------------------------------------------------------------------


async def test_stream_falls_back_before_first_token() -> None:
    groq = ScriptedProvider("groq", [error("auth", retryable=False)])
    anthropic = ScriptedProvider("anthropic", [reply("тон дружелюбный")])
    router, recorder, _ = make_router(groq, anthropic)

    events = [e async for e in router.stream(ask(), tier=Tier.SIMPLE)]

    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text.strip() == "тон дружелюбный"
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.response.is_fallback
    ok = recorder.calls[-1]
    assert ok.streamed
    assert ok.ttft_ms is not None


async def test_stream_failure_after_tokens_is_not_retried() -> None:
    groq = ScriptedProvider("groq", [FailMidStream("Начало", error("connection"))])
    router, _, _ = make_router(groq, ScriptedProvider("anthropic"))

    received: list[str] = []

    async def consume() -> None:
        async for event in router.stream(ask(), tier=Tier.SIMPLE):
            if isinstance(event, TextDelta):
                received.append(event.text)

    with pytest.raises(StreamInterruptedError):
        await consume()
    assert received == ["Начало"]


async def test_reasoning_keepalives_reset_the_idle_timeout() -> None:
    thinker = SlowThinker(reply("ответ после раздумий"), beats=6, interval=0.03)
    groq = ScriptedProvider("groq", [thinker])
    router, _, _ = make_router(groq, timeout_s=0.1, max_retries=0, fallback=[])

    events = [e async for e in router.stream(ask(), tier=Tier.COMPLEX)]  # 0.18s > timeout

    assert all(isinstance(e, TextDelta | StreamDone) for e in events)  # keep-alives filtered
    assert "".join(e.text for e in events if isinstance(e, TextDelta)).strip() == (
        "ответ после раздумий"
    )


# ---- structured output --------------------------------------------------------------------


class Post(BaseModel):
    title: str
    hashtags: list[str]


async def test_structured_output_valid_first_time() -> None:
    groq = ScriptedProvider("groq", [reply('{"title": "Латте", "hashtags": ["#кофе"]}')])
    router, _, _ = make_router(groq)

    post = await router.structured(ask("Напиши пост"), Post)

    assert post == Post(title="Латте", hashtags=["#кофе"])
    request = groq.requests[0][1]
    assert request.json_schema == Post.model_json_schema()
    assert request.messages[0].role == "system"


async def test_structured_output_self_repairs_once() -> None:
    groq = ScriptedProvider(
        "groq",
        [reply('{"title": "Латте"}'), reply('```json\n{"title": "Латте", "hashtags": []}\n```')],
    )
    router, _, _ = make_router(groq)

    post = await router.structured(ask("Напиши пост"), Post)

    assert post.hashtags == []
    repair_prompt = groq.requests[1][1].messages[-1]
    assert repair_prompt.role == "user"
    assert "hashtags" in repair_prompt.content


async def test_structured_output_fails_after_one_repair() -> None:
    groq = ScriptedProvider("groq", [reply("не JSON"), reply("всё ещё не JSON")])
    router, _, _ = make_router(groq)

    with pytest.raises(StructuredOutputError) as exc_info:
        await router.structured(ask("Напиши пост"), Post)
    assert exc_info.value.raw == "всё ещё не JSON"


def test_parse_json_model_tolerates_prose_around_json() -> None:
    parsed = parse_json_model(Post, 'Вот результат: {"title": "A", "hashtags": ["#b"]} Готово.')
    assert parsed.title == "A"


# ---- classifier ---------------------------------------------------------------------------


async def test_llm_classifier_uses_cheap_model_answer() -> None:
    groq = ScriptedProvider("groq", [reply("complex")])
    router, recorder, _ = make_router(groq, classifier="llm")

    assert await router.classify("Расскажи о бренде") is Tier.COMPLEX
    assert groq.requests[0][0] == "openai/gpt-oss-20b"
    assert recorder.calls[0].purpose == "classifier"


async def test_llm_classifier_falls_back_to_heuristic_on_failure() -> None:
    groq = ScriptedProvider("groq", [error("auth", retryable=False)])
    router, _, _ = make_router(groq, fallback=[], classifier="llm")
    assert await router.classify("Составь контент-план на неделю") is Tier.COMPLEX


# ---- persistence (real Postgres) ---------------------------------------------------------


async def test_calls_are_persisted_with_cost(settings: Settings, migrated_db: str) -> None:
    engine = create_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    groq = ScriptedProvider(
        "groq", [error("rate_limit"), reply(tokens_in=1_000_000, tokens_out=1_000_000)]
    )
    router = LLMRouter(
        LLMSettings(provider="groq"), {"groq": groq}, DbCallRecorder(sessionmaker), sleep=SleepSpy()
    )
    trace_id = uuid.uuid4().hex
    user_id = uuid.uuid4()

    await router.chat(ask(), tier=Tier.COMPLEX, ctx=CallContext(user_id=user_id, trace_id=trace_id))

    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(LLMCall).where(LLMCall.trace_id == trace_id).order_by(LLMCall.attempt)
            )
        ).all()
    await engine.dispose()

    assert [(r.status, r.error_type) for r in rows] == [("error", "rate_limit"), ("ok", None)]
    ok = rows[1]
    assert ok.model == "openai/gpt-oss-120b"
    assert ok.user_id == user_id
    assert ok.cost_usd == Decimal("0.750000")  # 1M in x $0.15 + 1M out x $0.60
