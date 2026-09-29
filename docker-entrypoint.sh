#!/bin/sh

# Preserve one-off container commands such as:
# docker compose run --rm fastapi alembic check
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "[DB] Running alembic migrations..."
alembic upgrade head

echo "[APP] Starting FastAPI..."
exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}"
