"""Test fixtures run against real Postgres and Redis (docker compose locally, services in CI)."""

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

os.environ["APP_ENV"] = "test"  # must be set before brand_api modules read settings

import asyncpg
import httpx
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from brand_api.config import AppEnv, Settings
from brand_api.db import create_engine, create_sessionmaker, normalize_database_url
from brand_api.main import create_app
from brand_api.seed import run_seed
from brand_mcp.config import McpSettings
from brand_mcp.deps import Deps
from brand_mcp.rag.seed import seed_documents
from brand_mcp.server import create_app as create_mcp_app
from brand_shared.permissions import Role

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://brand:brand@localhost:5433/brand_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
DEMO_PASSWORD = "demo-password-for-tests"
MCP_SECRET = "api-test-mcp-secret-" + "x" * 24
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
REPO_ROOT = Path(__file__).resolve().parents[3]


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
def base_settings() -> Settings:
    return Settings(
        app_env=AppEnv.TEST,
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        demo_password=DEMO_PASSWORD,
        demo_mode=True,
        mcp_internal_secret=MCP_SECRET,
    )


@pytest.fixture(scope="session")
async def mcp_url(base_settings: Settings, seeded: None) -> AsyncIterator[str]:
    """The real MCP tool server, served by uvicorn on a free port."""
    mcp_settings = McpSettings(
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        mcp_internal_secret=MCP_SECRET,
        log_level="WARNING",
        model_cache_dir=str(REPO_ROOT / ".cache" / "fastembed"),
        seed_documents_dir=str(REPO_ROOT / "data" / "brands"),
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            create_mcp_app(mcp_settings), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes only a bool flag
        await asyncio.sleep(0.02)
    # Clients exist (seeded), so the server's background ingestion finishes in one pass.
    deps = Deps.create(mcp_settings)
    await seed_documents(deps, REPO_ROOT / "data" / "brands", attempts=1)
    await deps.aclose()
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    await task


@pytest.fixture(scope="session")
def settings(base_settings: Settings, mcp_url: str) -> Settings:
    return base_settings.model_copy(update={"mcp_url": mcp_url})


@pytest.fixture(scope="session")
async def migrated_db(base_settings: Settings) -> str:
    await _ensure_database(base_settings.database_url)
    # env.py calls asyncio.run(), so migrations run in a worker thread.
    await asyncio.to_thread(_upgrade_head, base_settings.database_url)
    return base_settings.database_url


@pytest.fixture(scope="session")
async def seeded(base_settings: Settings, migrated_db: str) -> None:
    engine = create_engine(base_settings.database_url)
    async with create_sessionmaker(engine)() as session:
        await run_seed(session, base_settings)
    await engine.dispose()


@pytest.fixture
async def app(settings: Settings, seeded: None) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def demo_headers(client: httpx.AsyncClient, role: Role) -> dict[str, str]:
    response = await client.post("/auth/demo-login", json={"role": role.value})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def as_role(client: httpx.AsyncClient) -> "RoleHeaders":
    async def get(role: Role) -> dict[str, str]:
        return await demo_headers(client, role)

    return get


RoleHeaders = Callable[[Role], Awaitable[dict[str, str]]]
