import uuid
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from brand_mcp.config import McpSettings
from brand_mcp.rag.models import (
    Embedder,
    FastembedEmbedder,
    FastembedReranker,
    GeminiEmbedder,
    Reranker,
)
from brand_mcp.rag.search import HybridRetriever
from brand_shared.db.engine import create_engine, create_sessionmaker
from brand_shared.schemas.tools import BrandbookHit


class Retriever(Protocol):
    threshold: float

    async def search(
        self, session: AsyncSession, client_id: uuid.UUID, query: str, top_k: int
    ) -> list[BrandbookHit]: ...


def build_embedder(settings: McpSettings) -> Embedder:
    if settings.embedding_provider == "gemini":
        if settings.gemini_api_key is None:
            raise ValueError("EMBEDDING_PROVIDER=gemini requires GEMINI_API_KEY")
        return GeminiEmbedder(
            settings.gemini_api_key.get_secret_value(), settings.gemini_embedding_model
        )
    return FastembedEmbedder(settings.embedding_model, cache_dir=settings.model_cache_dir)


@dataclass
class Deps:
    settings: McpSettings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    embedder: Embedder
    reranker: Reranker | None
    retriever: Retriever

    @classmethod
    def create(cls, settings: McpSettings, embedder: Embedder | None = None) -> "Deps":
        engine = create_engine(settings.database_url)
        embedder = embedder or build_embedder(settings)
        reranker: Reranker | None = (
            FastembedReranker(settings.reranker_model, cache_dir=settings.model_cache_dir)
            if settings.reranker_enabled
            else None
        )
        return cls(
            settings=settings,
            engine=engine,
            sessionmaker=create_sessionmaker(engine),
            redis=Redis.from_url(settings.redis_url, decode_responses=True),
            embedder=embedder,
            reranker=reranker,
            retriever=HybridRetriever(embedder, reranker, settings),
        )

    async def aclose(self) -> None:
        await self.redis.aclose()
        await self.engine.dispose()
