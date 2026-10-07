"""Async SQLAlchemy engine/session factory with managed-Postgres URL normalisation."""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# libpq-only query params that asyncpg rejects as unknown connect kwargs.
_LIBPQ_ONLY_PARAMS = ("sslmode", "channel_binding", "options", "sslrootcert")


@dataclass(frozen=True)
class DatabaseConfig:
    url: URL
    connect_args: dict[str, Any] = field(default_factory=dict)

    @property
    def url_string(self) -> str:
        return self.url.render_as_string(hide_password=False)


def normalize_database_url(raw: str) -> DatabaseConfig:
    """Turn any libpq-style URL (Neon, Render, local) into an asyncpg-compatible config.

    - `postgres://` / `postgresql://` -> `postgresql+asyncpg://`
    - `sslmode=require` -> asyncpg `ssl="require"` (Neon requires TLS)
    - Neon pooled hosts (`-pooler`) run PgBouncer in transaction mode, so prepared
      statement caches must be disabled.
    """
    url = make_url(raw)
    if url.drivername in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        url = url.set(drivername="postgresql+asyncpg")

    query = dict(url.query)
    connect_args: dict[str, Any] = {}
    sslmode = query.get("sslmode")
    if isinstance(sslmode, str) and sslmode != "disable":
        connect_args["ssl"] = sslmode
    query = {k: v for k, v in query.items() if k not in _LIBPQ_ONLY_PARAMS}

    if url.host and "-pooler" in url.host:
        connect_args["statement_cache_size"] = 0  # asyncpg's own cache
        query["prepared_statement_cache_size"] = "0"  # SQLAlchemy dialect cache (URL param)
    url = url.set(query=query)

    return DatabaseConfig(url=url, connect_args=connect_args)


def create_engine(database_url: str) -> AsyncEngine:
    config = normalize_database_url(database_url)
    return create_async_engine(
        config.url,
        connect_args=config.connect_args,
        pool_pre_ping=True,  # serverless Postgres (Neon) drops idle connections
        pool_recycle=300,
        pool_size=5,
        max_overflow=5,
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
