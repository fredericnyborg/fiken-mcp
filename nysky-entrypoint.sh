#!/bin/sh
# Container-entrypoint på nysky. nysky injiserer datastore-URL-en som
# HOVED_DATABASE_URL; appen leser DATABASE_URL.
set -e

export DATABASE_URL="${DATABASE_URL:-$HOVED_DATABASE_URL}"
if [ -z "$DATABASE_URL" ]; then
  echo "FATAL: verken DATABASE_URL eller HOVED_DATABASE_URL er satt" >&2
  exit 1
fi

# Start uvicorn rett fra det ferdigbygde venv-et – IKKE `uv run`, som prøver å
# synke mot PyPI ved oppstart og feiler bak nyskys låste egress.
exec /app/.venv/bin/uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips '*'
