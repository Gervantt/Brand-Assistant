# Architecture decisions

Short ADR-style log. Each entry: context → decision → consequences.

## 001. uv workspace monorepo
**Context.** Three Python components (API gateway, MCP server, shared schemas) must share code and
pin identical dependency versions; Docker builds must be fast and reproducible.
**Decision.** One uv workspace (`packages/shared`, `apps/api`, later `apps/mcp_server`) with a
single `uv.lock`. Build backend `uv_build` (no extra build deps). uv also provisions Python 3.12,
so contributors don't need a system 3.12.
**Consequences.** `uv sync --all-packages` sets up everything; Dockerfiles install a single
package with `--package` against the same lockfile.

## 002. Settings: env for secrets, YAML profile for behaviour
**Context.** Same image must run locally (compose) and in production (Render) with different
models, thresholds and integrations; switching a model must be a one-line config change.
**Decision.** `pydantic-settings` with sources in order: env > `.env` > `config/<APP_ENV>.yaml` >
defaults. Secrets/URLs only ever come from env; empty env values are ignored so `.env.example`
can be copied as-is.
**Consequences.** Behaviour changes are reviewable in git; prod differs from local only by
`APP_ENV=prod` plus secrets.

## 003. Managed Postgres URL normalisation
**Context.** Neon hands out libpq URLs (`sslmode=require&channel_binding=require`), which asyncpg
does not understand; Neon's pooled endpoint is PgBouncer in transaction mode.
**Decision.** `normalize_database_url` converts any libpq URL to `postgresql+asyncpg`, maps
`sslmode` to asyncpg `ssl`, drops libpq-only params, and disables prepared-statement caches for
`-pooler` hosts. Engines use `pool_pre_ping` + `pool_recycle` because serverless Postgres drops
idle connections.
**Consequences.** `DATABASE_URL` can be pasted from the Neon console unchanged.

## 004. Pure-ASGI request middleware
**Context.** We need a `request_id` in every log line and response header, and the chat endpoint
streams SSE.
**Decision.** A pure ASGI middleware (not `BaseHTTPMiddleware`, which interferes with streaming)
binds `request_id` into structlog contextvars. Client-supplied `X-Request-ID` is accepted only if
it matches `[A-Za-z0-9._-]{1,64}` to prevent log injection.

## 005. Local host port for Postgres is 5433
**Context.** Developers often already run a Postgres on 5432.
**Decision.** Compose publishes Postgres on host port `5433` (override with `POSTGRES_PORT`).
Inside the compose network services still use `postgres:5432`.
