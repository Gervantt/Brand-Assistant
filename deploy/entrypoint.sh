#!/bin/sh
# Render start command: migrate + seed (both idempotent), then serve on $PORT.
set -eu
alembic -c /app/apps/api/alembic.ini upgrade head
python -m brand_api.seed
exec uvicorn brand_api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips '*' \
    --no-access-log \
    --timeout-keep-alive 75
