#!/bin/sh
# Idempotent startup: migrate, then serve. Safe to run on every container start.
set -eu

echo '{"event": "migrations_start"}'
alembic -c /app/apps/api/alembic.ini upgrade head

exec uvicorn brand_api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips '*' \
    --no-access-log
