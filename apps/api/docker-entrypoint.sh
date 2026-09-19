#!/bin/sh
# Production start: migrate (unless a release step already did), then serve.
set -e
# Fail fast on unsafe or invalid configuration (a worker supervisor would otherwise
# keep restarting workers that crash on import).
python -c "import app.config" >/dev/null
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --no-access-log
