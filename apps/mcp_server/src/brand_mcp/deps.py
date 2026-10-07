from dataclasses import dataclass, field
from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from brand_mcp.config import McpSettings
from brand_shared.db.engine import create_engine, create_sessionmaker
from brand_shared.schemas.tools import BrandbookHit


class Retriever(Protocol):
    async def search(
        self, session: AsyncSession, client_id: object, query: str, top_k: int
    ) -> list[BrandbookHit]: ...


class NullRetriever:
    """Placeholder until documents are ingested (RAG lands in phase 5): finds nothing."""

    async def search(
        self, session: AsyncSession, client_id: object, query: str, top_k: int
    ) -> list[BrandbookHit]:
        return []


@dataclass
class Deps:
    settings: McpSettings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    retriever: Retriever = field(default_factory=NullRetriever)

    @classmethod
    def create(cls, settings: McpSettings) -> "Deps":
        engine = create_engine(settings.database_url)
        return cls(
            settings=settings,
            engine=engine,
            sessionmaker=create_sessionmaker(engine),
            redis=Redis.from_url(settings.redis_url, decode_responses=True),
        )

    async def aclose(self) -> None:
        await self.redis.aclose()
        await self.engine.dispose()
