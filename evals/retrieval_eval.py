"""Retrieval quality per configuration: recall@k, MRR@10, abstention on off-topic questions."""

import time
import uuid
from dataclasses import asdict, dataclass

from brand_mcp.deps import Deps
from brand_mcp.rag.search import HybridRetriever, RetrievalMode
from brand_shared.schemas.tools import BrandbookHit
from evals.common import Case, mcp_settings, percentile

CONFIGS: list[tuple[str, RetrievalMode, bool]] = [
    ("vector only", "vector", False),
    ("lexical only (IDF)", "lexical", False),
    ("hybrid (RRF)", "hybrid", False),
    ("hybrid + reranker", "hybrid", True),
]


@dataclass
class RetrievalReport:
    config: str
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr_at_10: float
    found_rate_answerable: float  # should be high: confident when the answer exists
    abstention_rate_offtopic: float  # should be high: "not in the brand book"
    p50_latency_ms: float
    p95_latency_ms: float
    misses_at_5: list[str]


def first_relevant_rank(hits: list[BrandbookHit], evidence: list[str]) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if any(snippet in hit.text for snippet in evidence):
            return rank
    return None


async def evaluate(
    cases: list[Case], clients: dict[str, uuid.UUID], name: str, mode: RetrievalMode, rerank: bool
) -> RetrievalReport:
    settings = mcp_settings(reranker=rerank)
    deps = Deps.create(settings)
    retriever = HybridRetriever(deps.embedder, deps.reranker, settings, mode=mode)
    await deps.embedder.embed_query("прогрев")  # exclude model loading from latency
    if deps.reranker is not None:
        await deps.reranker.rerank("прогрев", ["прогрев"])

    ranks: dict[str, int | None] = {}
    found: dict[str, bool] = {}
    latencies: list[float] = []
    for case in cases:
        started = time.perf_counter()
        async with deps.sessionmaker() as session:
            hits = await retriever.search(session, clients[case.client], case.question, 10)
        latencies.append((time.perf_counter() - started) * 1000)
        found[case.id] = max((h.score for h in hits), default=0.0) >= retriever.threshold
        if case.answerable:
            ranks[case.id] = first_relevant_rank(hits, case.evidence)
    await deps.aclose()

    answerable = [c for c in cases if c.answerable]
    offtopic = [c for c in cases if not c.answerable]

    def recall(k: int) -> float:
        hit = sum(1 for c in answerable if (r := ranks[c.id]) is not None and r <= k)
        return hit / len(answerable)

    mrr = sum(1 / r for c in answerable if (r := ranks[c.id]) is not None) / len(answerable)
    return RetrievalReport(
        config=name,
        recall_at_1=round(recall(1), 3),
        recall_at_3=round(recall(3), 3),
        recall_at_5=round(recall(5), 3),
        mrr_at_10=round(mrr, 3),
        found_rate_answerable=round(sum(found[c.id] for c in answerable) / len(answerable), 3),
        abstention_rate_offtopic=round(
            sum(not found[c.id] for c in offtopic) / max(len(offtopic), 1), 3
        ),
        p50_latency_ms=round(percentile(latencies, 0.5), 1),
        p95_latency_ms=round(percentile(latencies, 0.95), 1),
        misses_at_5=[c.id for c in answerable if (r := ranks[c.id]) is None or r > 5],
    )


async def run(cases: list[Case], clients: dict[str, uuid.UUID]) -> list[dict[str, object]]:
    reports = []
    for name, mode, rerank in CONFIGS:
        report = await evaluate(cases, clients, name, mode, rerank)
        print(
            f"{name:22} R@1={report.recall_at_1:.2f} R@3={report.recall_at_3:.2f} "
            f"R@5={report.recall_at_5:.2f} MRR={report.mrr_at_10:.2f} "
            f"abstain={report.abstention_rate_offtopic:.2f} p95={report.p95_latency_ms:.0f}ms"
        )
        reports.append(asdict(report))
    return reports
