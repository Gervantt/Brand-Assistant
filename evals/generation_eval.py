"""Answer quality per model with an LLM judge.

Each model answers from the same retrieved fragments (production retrieval), using the same
grounding rules as the agent; a fixed judge model grades correctness against the reference
answer, groundedness, and whether off-topic questions were correctly declined.
"""

import time
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from brand_api.config import Settings
from brand_api.llm.config import LLMSettings, TierModels
from brand_api.llm.errors import AllModelsFailedError
from brand_api.llm.pricing import cost_usd
from brand_api.llm.registry import build_providers
from brand_api.llm.router import LLMRouter
from brand_api.llm.types import ChatRequest, Message, ModelRef, Tier
from brand_api.observability.llm_calls import NullRecorder
from brand_mcp.deps import Deps
from evals.common import Case, mcp_settings, percentile

ANSWER_RULES = (
    "Ты — ассистент креативного агентства. Отвечай по-русски, кратко, только по фрагментам "
    "брендбука ниже, ссылаясь на них как [1], [2]. Если ответа во фрагментах нет — прямо скажи, "
    "что в брендбуке этого нет. Не используй общие знания."
)

JUDGE_RULES = (
    "Ты — строгий проверяющий. Сравни ответ ассистента с эталоном. "
    "correctness: 2 — все ключевые факты эталона верны; 1 — частично; 0 — неверно или нет ответа. "
    "grounded: true, если ответ не содержит фактов, противоречащих фрагментам или выдуманных. "
    "abstained: true, если ассистент сказал, что в брендбуке этого нет."
)


class Verdict(BaseModel):
    correctness: Literal[0, 1, 2]
    grounded: bool
    abstained: bool
    comment: str = Field(default="", max_length=300)


@dataclass
class GenerationReport:
    model: str
    accuracy: float  # share of answerable questions judged fully correct
    partial: float
    grounded: float
    abstention_offtopic: float  # declined when the answer is not in the brand book
    false_abstention: float  # declined although the answer was available
    p50_latency_ms: float
    p95_latency_ms: float
    tokens_in: int
    tokens_out: int
    cost_usd: float
    errors: int


def _rate(flags: list[bool]) -> float:
    return round(sum(flags) / max(len(flags), 1), 3)


def single_model_router(settings: Settings, ref: ModelRef) -> LLMRouter:
    llm = LLMSettings(
        provider=ref.provider,
        providers={ref.provider: TierModels(simple=ref.model, complex=ref.model)},
        fallback=[],
        max_retries=4,
        backoff_max_s=65,  # free tiers have per-minute token windows: wait them out
        cache_enabled=False,
    )
    return LLMRouter(llm, build_providers(settings), NullRecorder())


class ModelUnavailableError(RuntimeError):
    pass


async def preflight(router: LLMRouter, model: str) -> None:
    """One tiny call per model before spending 90 calls on a model we can't use."""
    try:
        await router.chat(
            ChatRequest(messages=[Message(role="user", content="ok")], max_tokens=64),
            tier=Tier.SIMPLE,
        )
    except AllModelsFailedError as exc:
        reasons = "; ".join(f"{a.kind}: {a.message[:160]}" for a in exc.attempts)
        raise ModelUnavailableError(f"{model} is not usable: {reasons}") from exc


def fragments_prompt(question: str, hits: list[str]) -> str:
    if not hits:
        return f"Фрагментов не найдено.\n\nВопрос: {question}"
    numbered = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(hits, start=1))
    return f"Фрагменты брендбука:\n{numbered}\n\nВопрос: {question}"


async def run(
    cases: list[Case], clients: dict[str, uuid.UUID], models: list[str], judge: str
) -> list[dict[str, object]]:
    settings = Settings()
    retrieval_settings = mcp_settings(reranker=False)  # production configuration
    deps = Deps.create(retrieval_settings)
    contexts: dict[str, list[str]] = {}
    for case in cases:
        async with deps.sessionmaker() as session:
            hits = await deps.retriever.search(session, clients[case.client], case.question, 5)
        found = max((h.score for h in hits), default=0) >= deps.retriever.threshold
        contexts[case.id] = [f"({h.section}) {h.text}" for h in hits] if found else []
    await deps.aclose()

    judge_ref = ModelRef.parse(judge)
    judge_router = single_model_router(settings, judge_ref)
    await preflight(judge_router, judge)
    for model in models:
        await preflight(single_model_router(settings, ModelRef.parse(model)), model)
    reports = []
    for model in models:
        ref = ModelRef.parse(model)
        router = single_model_router(settings, ref)
        verdicts: dict[str, Verdict] = {}
        latencies: list[float] = []
        tokens_in = tokens_out = errors = 0
        cost = Decimal(0)
        for case in cases:
            prompt = fragments_prompt(case.question, contexts[case.id])
            started = time.perf_counter()
            try:
                response = await router.chat(
                    ChatRequest(
                        messages=[
                            Message(role="system", content=ANSWER_RULES),
                            Message(role="user", content=prompt),
                        ],
                        max_tokens=1024,
                        temperature=0.2,
                    ),
                    tier=Tier.SIMPLE,
                )
            except Exception as exc:  # keep evaluating; count the failure
                errors += 1
                print(f"  {model} {case.id}: {type(exc).__name__}")
                continue
            latencies.append((time.perf_counter() - started) * 1000)
            tokens_in += response.usage.input_tokens
            tokens_out += response.usage.output_tokens
            cost += cost_usd(settings.llm.pricing, ref, response.usage)
            reference = case.answer or "В брендбуке нет ответа; ассистент должен отказаться."
            verdicts[case.id] = await judge_router.structured(
                ChatRequest(
                    messages=[
                        Message(role="system", content=JUDGE_RULES),
                        Message(
                            role="user",
                            content=(
                                f"{prompt}\n\nЭталон: {reference}\n\n"
                                f"Ответ ассистента: {response.content}"
                            ),
                        ),
                    ],
                    max_tokens=1024,
                    temperature=0,
                ),
                Verdict,
                tier=Tier.SIMPLE,
            )
        answerable = [c for c in cases if c.answerable and c.id in verdicts]
        offtopic = [c for c in cases if not c.answerable and c.id in verdicts]
        report = GenerationReport(
            model=model,
            accuracy=_rate([verdicts[c.id].correctness == 2 for c in answerable]),
            partial=_rate([verdicts[c.id].correctness == 1 for c in answerable]),
            grounded=_rate([verdicts[c.id].grounded for c in [*answerable, *offtopic]]),
            abstention_offtopic=_rate([verdicts[c.id].abstained for c in offtopic]),
            false_abstention=_rate([verdicts[c.id].abstained for c in answerable]),
            p50_latency_ms=round(percentile(latencies, 0.5), 1),
            p95_latency_ms=round(percentile(latencies, 0.95), 1),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=float(cost),
            errors=errors,
        )
        print(
            f"{model:32} acc={report.accuracy:.2f} grounded={report.grounded:.2f} "
            f"abstain={report.abstention_offtopic:.2f} p95={report.p95_latency_ms:.0f}ms "
            f"cost=${report.cost_usd:.4f} errors={errors}"
        )
        reports.append(asdict(report))
        await router.aclose()
    await judge_router.aclose()
    return reports
