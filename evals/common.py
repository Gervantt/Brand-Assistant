"""Shared eval setup: a dedicated database with the demo brand books ingested."""

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import select

from brand_api.config import AppEnv, Settings
from brand_api.seed import run_seed
from brand_mcp.config import McpSettings
from brand_mcp.deps import Deps
from brand_mcp.rag.seed import seed_documents
from brand_shared.db.engine import create_engine, create_sessionmaker, normalize_database_url
from brand_shared.db.models import Client

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "brandbook_qa.jsonl"
RESULTS = ROOT / "evals" / "results"
MODEL_CACHE = str(ROOT / ".cache" / "fastembed")
EVAL_DATABASE_URL = os.environ.get(
    "EVAL_DATABASE_URL", "postgresql://brand:brand@localhost:5433/brand_eval"
)
EVAL_REDIS_URL = os.environ.get("EVAL_REDIS_URL", "redis://localhost:6379/14")


@dataclass(frozen=True)
class Case:
    id: str
    client: str
    question: str
    answer: str | None  # None = not in the brand book (the assistant must abstain)
    evidence: list[str]

    @property
    def answerable(self) -> bool:
        return self.answer is not None


def load_cases() -> list[Case]:
    rows = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    return [Case(**row) for row in rows]


def mcp_settings(*, reranker: bool) -> McpSettings:
    return McpSettings(
        database_url=EVAL_DATABASE_URL,
        redis_url=EVAL_REDIS_URL,
        model_cache_dir=MODEL_CACHE,
        reranker_enabled=reranker,
        log_level="WARNING",
    )


async def prepare_database() -> dict[str, uuid.UUID]:
    """Create/migrate the eval DB, seed clients and ingest data/brands. Idempotent."""
    db = normalize_database_url(EVAL_DATABASE_URL).url
    conn = await asyncpg.connect(
        user=db.username, password=db.password, host=db.host, port=db.port, database="postgres"
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db.database):
            await conn.execute(f'CREATE DATABASE "{db.database}"')
    finally:
        await conn.close()

    def upgrade() -> None:
        cfg = Config(str(ROOT / "apps" / "api" / "alembic.ini"))
        cfg.attributes["database_url"] = EVAL_DATABASE_URL
        command.upgrade(cfg, "head")

    await asyncio.to_thread(upgrade)
    engine = create_engine(EVAL_DATABASE_URL)
    async with create_sessionmaker(engine)() as session:
        await run_seed(
            session,
            Settings(
                app_env=AppEnv.TEST,
                database_url=EVAL_DATABASE_URL,
                demo_password=SecretStr("eval-only"),
            ),
        )
        clients = {c.slug: c.id for c in (await session.scalars(select(Client))).all()}
    await engine.dispose()

    deps = Deps.create(mcp_settings(reranker=False))
    await seed_documents(deps, ROOT / "data" / "brands", attempts=1)
    await deps.aclose()
    return clients


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(q * (len(ordered) - 1)))
    return ordered[index]
