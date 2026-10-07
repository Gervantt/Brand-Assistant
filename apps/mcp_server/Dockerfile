# syntax=docker/dockerfile:1.7
# Build context: repository root (needs the uv workspace + shared package).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/
COPY apps/api/pyproject.toml apps/api/
COPY apps/mcp_server/pyproject.toml apps/mcp_server/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package brand-mcp --no-install-workspace

COPY packages/shared packages/shared
COPY apps/mcp_server apps/mcp_server
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package brand-mcp --no-editable

# Demo brand books, ingested at startup (idempotent).
COPY data/brands data/brands
ENV SEED_DOCUMENTS_DIR=/app/data/brands \
    MODEL_CACHE_DIR=/models

RUN useradd --create-home --uid 10001 app && mkdir -p /models && chown app /models
USER app

EXPOSE 8001
CMD ["python", "-m", "brand_mcp.server"]
