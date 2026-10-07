"""Test fixtures run against real Postgres and Redis (docker compose locally, services in CI)."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

os.environ["APP_ENV"] = "test"  # must be set before brand_api modules read settings

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from brand_api.config import AppEnv, Settings
from brand_api.db import normalize_database_url
from brand_api.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://brand:brand@localhost:5433/brand_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


async def _ensure_database(url: str) -> None:
    db = normalize_database_url(url).url
    conn = await asyncpg.connect(
        user=db.username, password=db.password, host=db.host, port=db.port, database="postgres"
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db.database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db.database}"')
    finally:
        await conn.close()


def _upgrade_head(url: str) -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        app_env=AppEnv.TEST,
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
    )


@pytest.fixture(scope="session")
async def migrated_db(settings: Settings) -> str:
    await _ensure_database(settings.database_url)
    # env.py calls asyncio.run(), so migrations run in a worker thread.
    await asyncio.to_thread(_upgrade_head, settings.database_url)
    return settings.database_url


@pytest.fixture
async def app(settings: Settings, migrated_db: str) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
