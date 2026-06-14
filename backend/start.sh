#!/usr/bin/env bash
# Railway entrypoint for the ForgeSOP Platform Admin backend.
#
# One image, two roles selected by $SERVICE_ROLE:
#   web    (default) — run platform-schema migrations, then serve the API on $PORT
#   worker           — run the Celery worker + embedded beat (footprint / metrics
#                      rollups, alert sweeps, operator digest)
set -euo pipefail

if [ "${SERVICE_ROLE:-web}" = "worker" ]; then
  echo "[start] launching celery worker + beat"
  exec celery -A app.tasks.celery_app worker --beat \
    --scheduler celery.beat.PersistentScheduler \
    --loglevel=info --concurrency="${CELERY_CONCURRENCY:-2}"
fi

# web role. The platform-schema migrations are Postgres-specific (CREATE SCHEMA
# platform, jsonb, inet ...), so only run them once DATABASE_URL is a real
# Postgres URL. Until then the API still boots green on /health.
case "${DATABASE_URL:-}" in
  postgresql*)
    echo "[start] alembic upgrade head"
    python -m alembic upgrade head
    ;;
  *)
    echo "[start] DATABASE_URL is not a postgres URL — skipping migrations"
    ;;
esac

echo "[start] launching uvicorn on :${PORT:-8000}"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
