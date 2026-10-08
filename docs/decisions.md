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

## 011. Auth: stateless JWT, but permissions read from the DB on every request
**Context.** Simple login without an external IdP; revoking a role or a client must work now,
not when the token expires.
**Decision.** HS256 access tokens (PyJWT) carry only `sub`, `type` and `exp`. Each request
loads the user (role, `is_active`, client assignments) from Postgres. Passwords use `bcrypt`
directly (passlib is unmaintained); inputs over bcrypt's 72-byte limit are rejected at the API
instead of being silently truncated. Unknown emails cost the same bcrypt time as wrong passwords
(no account enumeration by timing); both return the same message. Production refuses to start
with the dev `JWT_SECRET` or one shorter than 32 characters.

## 012. RBAC + ABAC model
**Decision.** `brand_shared.permissions` is the single source of truth, shared by the gateway
and the MCP server. Roles are cumulative (viewer ⊂ copywriter ⊂ manager ⊂ admin) over a small
set of *actions*; every MCP tool maps to exactly one action, and unknown tool names are denied.
ABAC is the user's client assignment list (`user_clients`); admins implicitly access every
client. An inaccessible client returns 404, same as a non-existent one, so client ids can't be
probed. Self-registration creates a viewer with no clients (useless until an admin assigns
some), and can be switched off with `ALLOW_REGISTRATION=false`.

## 013. Demo accounts for a public demo
**Decision.** Seed creates one `is_demo` account per role and re-syncs role, clients and
`DEMO_PASSWORD` on every start, so vandalism through the public demo heals on restart. The
login page uses `POST /auth/demo-login {role}` — the frontend never sees the demo password.
Demo accounts can't be edited through the admin API. `DEMO_MODE=false` turns all of this off.

## 014. MCP SDK v2, streamable HTTP, one session per agent run
**Context.** The tool server is a separate process speaking MCP. The current SDK is `mcp` 2.x
(`MCPServer`, `Client`, protocol revision 2026-07-28).
**Decision.** The gateway opens one MCP session per agent run, authenticated with a short-lived
service JWT (`MCP_INTERNAL_SECRET`, audience `brand-mcp`) that carries the end user's id, role,
client ids and the trace id. An ASGI gate on the MCP server answers 401 before MCP parses
anything when the token is missing/invalid. DNS-rebinding protection is off: the endpoint is
never browser-facing and every request is authenticated.

## 015. Three permission checks per tool call
1. **Before the model sees tools** — the gateway filters the MCP catalogue by role
   (`llm_tools_for`); viewers literally don't know generation tools exist.
2. **On every call the model makes** — the gateway re-checks the name against the offered set
   and the role (the model can still emit any name, e.g. after prompt injection). Blocked calls
   are never sent to MCP; the model gets an error result and the attempt is logged.
3. **On the MCP server** — the first resolver of every tool re-checks role and client access
   from the service token. Because resolvers run before the tool body, this is what guarantees
   a denied call never triggers LLM sampling.
`client_id` is removed from the tool schemas shown to the model and injected by the gateway from
the conversation, so a model cannot point a tool at another client.

## 016. Generation via MCP sampling with resolver DAGs
**Context.** Plan/post/brief generation needs an LLM inside the tool, but cost accounting,
routing, fallback and the token budget must stay in one place.
**Decision.** Generation tools request completions from the client through MCP sampling
(`Resolve(fn)` returning `Sample`, SDK v2). The gateway's `SamplingBridge` answers with the LLM
router (`purpose=sampling:<tool>` in `llm_calls`), passing the JSON schema from sampling
metadata so providers use JSON mode. Per tool the DAG is
`authorize → prompt (brand context from DB) → first sample → accept or one repair sample`.
After a second invalid answer the tool returns a clear `ToolError`.
**Trade-off.** More protocol round trips than generating in the gateway, but tools stay
self-contained and reusable by any MCP host that supports sampling.

## 017. Drafts in Redis, publishing in Postgres
`create_content_plan` stores the validated plan as a Redis draft (24 h TTL) and returns a
`draft_id`. `publish_content_plan` — the only tool with a side effect — copies it into
`content_plans`. Publishing is idempotent (`source_draft_id` is unique; a concurrent duplicate
resolves to the existing row) and checks that the draft belongs to the conversation's client.
The UI's "Опубликовать" button calls `POST /plans/publish`, which goes through the same MCP tool.

## 018. Conversation state: Postgres transcript, Redis run state
The full transcript (user, assistant with tool calls, tool results, provider-native blocks) is
stored in `messages` so the next turn has the same context; history loads the last N messages
starting at a user turn. While a run is active, Redis holds a per-conversation lock (a second
message gets 409) and a small state record (`GET /conversations/{id}/state`). The transcript is
persisted in a cancellation-shielded `finally`, so a client disconnect mid-stream loses nothing.
Large artifacts go to the UI in full but reach the model as compact summaries (saves tokens on
Groq's 8K TPM free tier).

## 019. RAG: models, chunking, thresholds
**Embeddings.** fastembed `paraphrase-multilingual-MiniLM-L12-v2` (384-d, ~220 MB, ONNX, no
torch, no API key) — `bge-small` from the original spec is English-only and the content is
Russian. `EMBEDDING_PROVIDER=gemini` switches to `gemini-embedding-001` truncated to 384-d
(Matryoshka) and re-normalised, so both share the `vector(384)` column.
**Chunking.** Heading-aware: chunks never cross a section; the section path
(«Тон голоса > Эмодзи») is stored and embedded with the text; long sections are packed by
paragraph/sentence to ~1200 chars with a sentence-aligned ~200-char overlap.
**Thresholds.** Measured on the demo brand book: on-topic questions score 0.45–0.70 cosine,
off-topic 0.17–0.31 (one outlier: «Рецепт борща» 0.46 against a coffee brand book). Default
`MIN_SIMILARITY=0.40`; with the reranker `MIN_RERANK_SCORE=0.30` (reranker probabilities separate
much better: «борщ» → 0.16).

## 020. Hybrid search = pgvector + IDF-weighted lexical match, fused with RRF
Postgres `ts_rank_cd` is *not* BM25: it has no IDF, so «бренд» outweighed «слоган» in tests.
The lexical side therefore scores chunks BM25-style with binary term frequency (sum of IDF of
matched lexemes, per client), drops question-word stems and lexemes present in >50% of a
client's chunks, and ORs the rest (AND semantics kill natural questions). Candidates from both
sides (20 each) are fused with Reciprocal Rank Fusion (k=60). HNSW search runs with
`hnsw.iterative_scan = relaxed_order` so the per-client filter doesn't starve recall.
**Known limitation (measured).** Without the reranker, a chunk that only the lexical side finds
(«слоган» → «Позиционирование») can lose to chunks found by both sides. The cross-encoder
(`jina-reranker-v2-base-multilingual`) fixes these cases; it is on locally and off in prod
(1.1 GB doesn't fit Render's 512 MB). Evals quantify the difference.

## 021. Confidence: retrieval gate + model self-assessment
1. **Hard gate.** `search_brandbook` returns `found=false` when the best score is under the
   threshold; the gateway then gives the model *no fragments*, only the instruction to say
   «в брендбуке этого нет» and ask a clarifying question.
2. **Soft signal.** The model ends brand-book answers with `<confidence>x</confidence>`; a
   streaming filter removes the marker (even when split across tokens) before the user sees
   anything. `combined = 0.6·retrieval + 0.4·self`; below 0.5 the UI shows a "low confidence"
   badge (SSE `confidence` event, stored in the message meta).

## 022. Ingestion lives in the MCP server
Only the MCP process loads the embedding model. Uploads go `POST /clients/{id}/documents` →
internal MCP tool `ingest_document` (never offered to the model, RBAC `upload_documents`,
16 MB transport limit for a 10 MB file in base64). Documents are deduplicated per client by
SHA-256. The demo brand books in `data/brands/<slug>/` are ingested by the MCP server at
startup in the background (it waits for the API's seed to create the clients).

## 023. Tracing: Langfuse SDK v4 (OpenTelemetry), one trace per request
The gateway owns all tracing. The agent run is the root observation (`as_type="agent"`) of a
trace whose id is the run's `trace_id` — the same id that goes into JSON logs, `llm_calls` and
the MCP service token, so one id correlates everything. Every LLM attempt is a `generation`
(model, input, usage, cost, TTFT, ERROR level on failed attempts), including MCP-sampling
calls made from inside tools; every tool call is a `tool` observation. Without keys the tracer
is a no-op. Locally Langfuse v4 runs under the `observability` compose profile with a
pre-provisioned project; note that v4 runs in *events-only* mode, so the read API is
`/api/public/v2/observations?traceId=…` (the legacy `/traces` endpoint is disabled).

## 024. LLM response cache in Redis
Identical requests (same primary model, tier, messages, tools and parameters) are answered
from Redis (TTL 1 h). Hits are rows in `llm_calls` with `cached=true`, `cost_usd=0` and
`cost_saved_usd` = what the call would have cost, so savings are visible in
`/admin/metrics`. Only successful responses are cached; Redis failures degrade to a miss.
The cache is off in the test profile so scripted providers see every call.

## 025. /admin/metrics from llm_calls
Window-based (`?hours=`, default 24) aggregates computed in SQL: avg and p95 latency
(`percentile_cont`, cache hits excluded), cost per day and per model, error rate, fallback rate
(share of successful calls served by a fallback model), cache hits and savings, call counts per
purpose (agent / sampling / classifier).

## 026. Web UI: Vite + React + TypeScript + Mantine, minimal state
Mantine gives tables, cards, AppShell, Dropzone and notifications out of the box, which keeps the
UI at ~10% of the effort. No state library: auth and the selected client live in two React
contexts; the chat turn is a pure reducer over SSE events (`chat/turn.ts`, unit-tested with
Vitest). SSE goes through `@microsoft/fetch-event-source` (EventSource can't POST or send an
`Authorization` header) with auto-reconnect disabled — a retry would re-run the agent.
`react-markdown` renders answers. A tiny external store raises the "server is waking up" banner
when a request takes >5 s or fails at the network level (Render free tier cold start). The API
base URL comes from `VITE_API_URL`. Locally the same static build is served by nginx in compose
(`:5173`); in production Vercel hosts it.

## 027. Evals: evidence-quote relevance, ablations, judge with a fixed model
Retrieval relevance is decided by an exact evidence quote from the source document rather than
by chunk ids, so the dataset survives re-chunking. Four configurations are measured (vector,
lexical, hybrid, hybrid + reranker) on a dedicated `brand_eval` database built from scratch by the
script. Answer quality uses the production retrieval and the agent's grounding rules, one model at
a time with no fallback (so results are attributable), and a single fixed judge model with a
Pydantic verdict schema. Evals are not part of CI: they need real models and provider quota.

## 028. End-to-end smoke test
`scripts/smoke.py` exercises a running compose stack the way a user would: health, demo login
for every role, ABAC, ingested documents, the SSE agent stream, admin metrics and the web app.
It passes with or without an LLM key (without one it expects the explicit `llm_unavailable`
error), so CI can run it without secrets.

## 029. Production on Render free: one process, Gemini embeddings (measured)
Render's free web service has 512 MB RAM and private services aren't free, so production runs a
single container: the API with the MCP tool server mounted in-process (`MCP_MODE=embedded`),
still talking real MCP over HTTP on localhost with the signed service token.
Measured with `docker run --memory=512m` on the production image:
- the app with all libraries imported: ~185 MB;
- loading `paraphrase-multilingual-MiniLM-L12-v2` (241 MB on disk) adds ~560 MB resident
  (onnxruntime graph optimisation; disabling the CPU arena saves only ~15 MB) — the container was
  OOM-killed;
- with Gemini embeddings: **171 MB steady, 206 MB peak** while serving a full agent turn.
So production uses `EMBEDDING_PROVIDER=gemini` (free API key) and keeps the reranker off;
fastembed stays the default locally and on instances with ≥2 GB. English `bge-small` would fit but
retrieves Russian poorly. Two bugs this measurement surfaced and fixed: a race in lazy model
loading (warm-up and startup ingestion loaded two copies) and a 256-item default batch size.

## 030. Public-demo limits
`RATE_LIMIT_PER_HOUR` (default 20) agent messages per user — a fixed hourly window in Redis,
answered with 429, a readable message and `Retry-After`. Login is throttled per IP+email (10 per
15 min). `DAILY_TOKEN_BUDGET` caps total LLM tokens per UTC day across the demo (checked before
every LLM call, incremented after; cache hits are free) to protect the free Groq quota. Limiters
fail open if Redis is unavailable — an outage of the protection must not take the product down.

## 031. Degrade instead of failing
A live run in the production topology showed two failure amplifiers: (1) generation tools used
brand-book retrieval for context and failed entirely when the embeddings API failed — they now
fall back to the client profile; (2) the model retried a failing tool until the step limit —
the gateway now refuses a third call to a tool that already failed twice in the run and tells
the model to report the problem.

## 032. Per-provider similarity thresholds; Gemini batching
Running the retrieval eval with Gemini embeddings showed two things:
- **Retrieval is much stronger.** Hybrid Recall@1 is 0.88 vs 0.70 with MiniLM.
- **Cosine similarities sit on a different scale.** Answerable questions score ≥0.63, off-topic
  ones up to 0.68, so the MiniLM threshold (0.40) let every off-topic question through.

The "found" threshold is therefore calibrated per provider (`CALIBRATED_MIN_SIMILARITY`:
fastembed 0.40, gemini 0.66), overridable with `MIN_SIMILARITY`.

The same run also hit Gemini's free-tier limit, where each text in a batch counts as a request.
The embedder now sends batches of 20 and retries 429/5xx with backoff, honouring `Retry-After`.
Without this, startup ingestion on Render would fail.
