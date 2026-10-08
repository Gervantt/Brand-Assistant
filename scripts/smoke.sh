#!/bin/sh
# Bring the whole stack up and run the end-to-end smoke test against it.
set -eu
cd "$(dirname "$0")/.."
docker compose up -d --build --wait
uv run python scripts/smoke.py "$@"
