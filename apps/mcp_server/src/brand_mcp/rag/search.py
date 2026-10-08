"""Hybrid retrieval: pgvector cosine + Postgres full-text, fused with Reciprocal Rank Fusion,
optionally re-ordered by a cross-encoder. Every hit carries a calibrated-ish score in [0, 1]
(reranker probability, or cosine similarity when the reranker is off) used for confidence."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from brand_mcp.config import McpSettings
from brand_mcp.rag.models import Embedder, Reranker
from brand_shared.db.models import Chunk, Document
from brand_shared.schemas.tools import BrandbookHit

RRF_K = 60

# Lexical side. Plain Postgres FTS ranking (ts_rank_cd) has no IDF, so a frequent word like
# "бренд" outweighs a rare, decisive one like "слоган". We score chunks BM25-style with binary
# term frequency: the sum of IDF over matched query lexemes; length-normalised ts_rank_cd breaks
# ties. Question words (stems below) carry no signal and are removed; lexemes present in most of
# a client's chunks are dropped. Lexemes are OR-ed: AND semantics kill natural questions.
QUESTION_STEMS = [
    "как",
    "так",
    "что",
    "чем",
    "где",
    "куд",
    "когд",
    "скольк",
    "почем",
    "зач",
    "кто",
    "котор",
    "можн",
    "нужн",
    "ли",
    "есть",
    "эт",
    "все",
    "расскаж",
    "подскаж",
]
MAX_DOCUMENT_FREQUENCY = 0.5

_LEXICAL_SQL = text(
    """
    WITH total AS (SELECT count(*) AS n FROM chunks WHERE client_id = :client_id),
    terms AS (
        SELECT DISTINCT lexeme, to_tsquery('simple', quote_literal(lexeme)) AS tq
        FROM unnest(tsvector_to_array(to_tsvector('russian', :query))) AS lexeme
        WHERE lexeme <> ALL(CAST(:stop_stems AS text[]))
    ),
    weights AS (
        SELECT t.lexeme, t.tq, ln((total.n - s.df + 0.5) / (s.df + 0.5) + 1) AS idf
        FROM terms t
        CROSS JOIN total
        CROSS JOIN LATERAL (
            SELECT count(*) AS df FROM chunks c
            WHERE c.client_id = :client_id AND c.tsv @@ t.tq
        ) s
        WHERE s.df > 0 AND s.df <= greatest(1, total.n * CAST(:max_df AS double precision))
    ),
    q AS (
        SELECT to_tsquery(
            'simple', array_to_string(ARRAY(SELECT quote_literal(lexeme) FROM weights), ' | ')
        ) AS q
    )
    SELECT c.id
    FROM chunks c
    JOIN weights w ON c.tsv @@ w.tq
    CROSS JOIN q
    WHERE c.client_id = :client_id
    GROUP BY c.id, q.q
    ORDER BY sum(w.idf) DESC, ts_rank_cd(c.tsv, q.q, 2) DESC  -- 2: shorter, denser chunks win ties
    LIMIT :limit
    """
)


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[uuid.UUID]], k: int = RRF_K
) -> dict[uuid.UUID, float]:
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


@dataclass(frozen=True)
class _Candidate:
    id: uuid.UUID
    document: str
    section: str
    text: str
    similarity: float

    def for_rerank(self) -> str:
        return f"{self.section}\n{self.text}" if self.section else self.text


RetrievalMode = Literal["hybrid", "vector", "lexical"]


class HybridRetriever:
    """`mode` exists for evals (ablations); production always runs "hybrid"."""

    def __init__(
        self,
        embedder: Embedder,
        reranker: Reranker | None,
        settings: McpSettings,
        mode: RetrievalMode = "hybrid",
    ) -> None:
        self.embedder = embedder
        self.reranker = reranker
        self.mode = mode
        self.candidates = settings.rag_candidates
        self.threshold = settings.min_rerank_score if reranker else settings.min_similarity

    async def search(
        self, session: AsyncSession, client_id: uuid.UUID, query: str, top_k: int
    ) -> list[BrandbookHit]:
        vector = await self.embedder.embed_query(query)
        distance = Chunk.embedding.cosine_distance(vector)
        # With a per-client filter, iterative HNSW scans keep recall from collapsing.
        await session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
        vector_ids = list(
            (
                await session.scalars(
                    select(Chunk.id)
                    .where(Chunk.client_id == client_id)
                    .order_by(distance)
                    .limit(self.candidates)
                )
            ).all()
        )
        fts_ids = list(
            (
                await session.scalars(
                    _LEXICAL_SQL,
                    {
                        "query": query,
                        "client_id": client_id,
                        "limit": self.candidates,
                        "stop_stems": QUESTION_STEMS,
                        "max_df": MAX_DOCUMENT_FREQUENCY,
                    },
                )
            ).all()
        )
        rankings = {"hybrid": [vector_ids, fts_ids], "vector": [vector_ids], "lexical": [fts_ids]}
        fused = reciprocal_rank_fusion(rankings[self.mode])
        if not fused:
            return []

        rows = (
            await session.execute(
                select(
                    Chunk.id,
                    Document.title,
                    Chunk.section,
                    Chunk.text,
                    (1 - distance).label("similarity"),
                )
                .join(Document, Document.id == Chunk.document_id)
                .where(Chunk.id.in_(fused))
            )
        ).all()
        candidates = sorted(
            (_Candidate(r.id, r.title, r.section, r.text, float(r.similarity)) for r in rows),
            key=lambda c: fused[c.id],
            reverse=True,
        )

        if self.reranker is not None:
            probabilities = await self.reranker.rerank(query, [c.for_rerank() for c in candidates])
            scored = sorted(
                zip(candidates, probabilities, strict=True), key=lambda p: p[1], reverse=True
            )
        else:
            scored = [(c, c.similarity) for c in candidates]

        return [
            BrandbookHit(
                document=c.document,
                section=c.section,
                text=c.text,
                score=round(min(max(score, 0.0), 1.0), 3),
            )
            for c, score in scored[:top_k]
        ]
