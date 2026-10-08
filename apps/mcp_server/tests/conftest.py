"""MCP server tests: a real uvicorn server, real Postgres and Redis; only the LLM is scripted
(via the client's sampling callback)."""

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

os.environ["APP_ENV"] = "test"
os.environ["BRAND_ENV_FILE"] = ""  # ignore the developer's personal .env

import httpx2
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from mcp import Client
from mcp.client.session import SamplingFnT
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, CreateMessageRequestParams, TextContent
from pydantic import SecretStr
from sqlalchemy import select

from brand_api.config import AppEnv, Settings
from brand_api.seed import run_seed
from brand_mcp.config import McpSettings
from brand_mcp.deps import Deps
from brand_mcp.rag.seed import seed_documents
from brand_mcp.server import create_app
from brand_shared.db.engine import create_engine, create_sessionmaker, normalize_database_url
from brand_shared.db.models import Client as ClientRow
from brand_shared.db.models import User
from brand_shared.internal_auth import InternalClaims, bearer, encode_internal_token
from brand_shared.permissions import Role

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://brand:brand@localhost:5433/brand_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
SECRET = "mcp-test-secret-" + "x" * 24
REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"
BRANDS_DIR = REPO_ROOT / "data" / "brands"
MODEL_CACHE = str(REPO_ROOT / ".cache" / "fastembed")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def prepare_database() -> None:
    import asyncpg  # noqa: PLC0415

    db = normalize_database_url(TEST_DATABASE_URL).url
    conn = await asyncpg.connect(
        user=db.username, password=db.password, host=db.host, port=db.port, database="postgres"
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db.database):
            await conn.execute(f'CREATE DATABASE "{db.database}"')
    finally:
        await conn.close()

    def upgrade() -> None:
        cfg = Config(str(ALEMBIC_INI))
        cfg.attributes["database_url"] = TEST_DATABASE_URL
        command.upgrade(cfg, "head")

    await asyncio.to_thread(upgrade)
    api_settings = Settings(
        app_env=AppEnv.TEST,
        database_url=TEST_DATABASE_URL,
        demo_password=SecretStr("demo-password-for-tests"),
    )
    engine = create_engine(TEST_DATABASE_URL)
    async with create_sessionmaker(engine)() as session:
        await run_seed(session, api_settings)
    await engine.dispose()


def mcp_settings_for_tests() -> McpSettings:
    return McpSettings(
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        mcp_internal_secret=SecretStr(SECRET),
        log_level="WARNING",
        model_cache_dir=MODEL_CACHE,
    )


@pytest.fixture(scope="session")
def mcp_settings() -> McpSettings:
    return mcp_settings_for_tests()


@pytest.fixture(scope="session")
async def mcp_url(mcp_settings: McpSettings) -> AsyncIterator[str]:
    await prepare_database()
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(mcp_settings), host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes only a bool flag
        await asyncio.sleep(0.02)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    await task


@pytest.fixture(scope="session")
async def brandbooks(mcp_url: str, mcp_settings: McpSettings) -> None:
    """Demo brand books ingested with the real embedding model (idempotent)."""
    deps = Deps.create(mcp_settings)
    await seed_documents(deps, BRANDS_DIR, attempts=1)
    await deps.aclose()


class DemoIds:
    def __init__(self, users: dict[str, uuid.UUID], clients: dict[str, uuid.UUID]) -> None:
        self.users = users
        self.clients = clients


@pytest.fixture(scope="session")
async def ids(mcp_url: str) -> DemoIds:
    engine = create_engine(TEST_DATABASE_URL)
    async with create_sessionmaker(engine)() as session:
        users = {u.email: u.id for u in (await session.scalars(select(User))).all()}
        clients = {c.slug: c.id for c in (await session.scalars(select(ClientRow))).all()}
    await engine.dispose()
    return DemoIds(users, clients)


ROLE_ACCESS: dict[Role, tuple[str, ...]] = {
    Role.VIEWER: ("bean-there",),
    Role.COPYWRITER: ("bean-there",),
    Role.MANAGER: ("bean-there", "peakform"),
    Role.ADMIN: (),
}


def token_for(ids: DemoIds, role: Role) -> str:
    claims = InternalClaims(
        user_id=ids.users[f"{role.value}@demo.com"],
        role=role,
        client_ids=frozenset(ids.clients[s] for s in ROLE_ACCESS[role]),
        trace_id=uuid.uuid4().hex,
    )
    return encode_internal_token(claims, secret=SECRET)


@asynccontextmanager
async def connect(
    url: str, token: str, sampling: SamplingFnT | None = None
) -> AsyncIterator[Client]:
    http = httpx2.AsyncClient(headers={"Authorization": bearer(token)}, timeout=30)
    transport = streamable_http_client(url, http_client=http)
    async with Client(transport, sampling_callback=sampling) as client:
        yield client


def text_of(result: CallToolResult) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, TextContent))


def data(result: CallToolResult) -> dict[str, Any]:
    content: dict[str, Any] | None = result.structured_content
    assert content is not None, text_of(result)
    return content


def sampled_text(params: CreateMessageRequestParams, index: int = 0) -> str:
    content = params.messages[index].content
    assert isinstance(content, TextContent)
    return content.text
