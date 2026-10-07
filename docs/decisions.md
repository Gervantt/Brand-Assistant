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

## 006. Own provider abstraction over vendor SDKs (no LangChain/LiteLLM)
**Context.** We need chat, streaming, tool calling and structured output across Groq, OpenAI,
Anthropic and Ollama, with exact token/cost accounting and control over retries.
**Decision.** A small `LLMProvider` protocol with neutral types (`Message`, `ToolCall`,
`ChatResponse`). Two implementations cover four providers: `OpenAICompatProvider` (OpenAI, Groq
and Ollama all speak Chat Completions; they differ by `base_url` and two options) and
`AnthropicProvider` (Messages API). SDK-internal retries are disabled (`max_retries=0`) so the
router is the single retry layer and every attempt is logged/recorded exactly once.
**Consequences.** ~600 lines we own instead of a large dependency; adding a provider is one class.
Provider-native content that must be replayed verbatim (Anthropic thinking blocks) travels in
`Message.native` and is replayed only to the model that produced it.

## 007. Routing config: provider + tiers + fallback chain
**Context.** "Switching the model is a config change, not a code change."
**Decision.** `llm.provider` picks the primary provider; `llm.providers.<name>.{simple,complex}`
map tiers to models; `llm.fallback` is an ordered list of providers or exact `provider/model`
refs. Env overrides use `__` nesting: `LLM__PROVIDER=anthropic`. Providers without credentials
are silently skipped, so the same config works with any subset of keys.
On Groq's free tier each model has its own token-per-minute bucket, so the prod chain falls back
to *another Groq model* (`qwen/qwen3.8-27b`, then `gpt-oss-20b`) rather than another vendor.

## 008. Complexity routing: heuristic by default
**Context.** Simple brand-book Q&A should go to a cheap model; content generation to a strong one.
**Decision.** A regex heuristic (generation verbs and nouns: «контент-план», «напиши», «бриф»…,
plus length / multi-question signals) is the default: free, zero latency, and accurate for this
narrow domain. `llm.classifier: llm` switches to a cheap-model classifier that falls back to the
heuristic on any failure.

## 009. Retry, timeout and fallback policy
**Decision.** Per-call `asyncio.timeout` (idle timeout per stream event when streaming);
retryable errors (timeouts, connection, 429, 5xx, 529) are retried on the same model with
exponential backoff + jitter up to `max_retries`; a `Retry-After` longer than `backoff_max_s`
skips straight to the next model (waiting a minute for a TPM window is worse than falling back).
Non-retryable errors (auth, 400, refusal) fall back immediately. Streams can fall back only
before the first token; after that the user gets a clear "interrupted" error instead of a
spliced answer from two models. Structured output gets exactly one self-repair round with the
validation error fed back, then `StructuredOutputError`.

## 010. One `llm_calls` row per attempt
**Decision.** Every provider attempt (including failed retries) is a row with tokens, cost
(from the `llm.pricing` table, USD per 1M tokens), latency, TTFT, attempt number and
`is_fallback`. This makes error rate and fallback rate direct SQL aggregates. When a provider
omits usage in a stream, tokens are estimated and flagged `tokens_estimated`.
