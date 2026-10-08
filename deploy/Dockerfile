# syntax=docker/dockerfile:1.7
# Production image for Render's free tier: ONE container running the API gateway with the MCP
# tool server mounted in-process (MCP_MODE=embedded) — private services aren't free and the
# instance has 512 MB RAM. Embeddings default to the Gemini API there: the local multilingual
# model needs ~560 MB resident on its own (measured; see docs/decisions.md 029).
# Build context: repository root.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    CONFIG_DIR=/app/config \
    APP_ENV=prod \
    MCP_MODE=embedded \
    EMBEDDING_PROVIDER=gemini \
    MODEL_CACHE_DIR=/models \
    SEED_DOCUMENTS_DIR=/app/data/brands \
    RERANKER_ENABLED=false \
    OMP_NUM_THREADS=1

WORKDIR /app

# 1) Dependencies (cached until the lockfile changes). Dev group excluded.
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/
COPY apps/api/pyproject.toml apps/api/
COPY apps/mcp_server/pyproject.toml apps/mcp_server/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages --no-install-workspace

# 2) Source, config, demo brand books.
COPY packages/shared packages/shared
COPY apps/api apps/api
COPY apps/mcp_server apps/mcp_server
COPY config config
COPY data/brands data/brands
COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages --no-editable

RUN useradd --create-home --uid 10001 app && mkdir -p /models && chown -R app /models
USER app

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
