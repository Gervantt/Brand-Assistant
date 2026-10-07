# syntax=docker/dockerfile:1.7
# Build context: repository root (needs the uv workspace + shared package).
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    CONFIG_DIR=/app/config

WORKDIR /app

# 1) Dependencies only — cached until a lockfile or pyproject changes.
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/
COPY apps/api/pyproject.toml apps/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package brand-api --no-install-workspace

# 2) Source.
COPY packages/shared packages/shared
COPY apps/api apps/api
COPY config config
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package brand-api --no-editable

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
ENTRYPOINT ["/app/apps/api/docker-entrypoint.sh"]
