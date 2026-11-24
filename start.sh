#!/usr/bin/env bash
set -euo pipefail

# Match the same PostgreSQL URL aliases supported by app.py.
DB_URL_FOUND=0
for name in DATABASE_URL DATABASE_PRIVATE_URL DATABASE_PUBLIC_URL POSTGRES_URL POSTGRESQL_URL POSTGRES_URI; do
  if [[ -n "${!name:-}" ]]; then DB_URL_FOUND=1; break; fi
done

# Or accept a complete set of split PostgreSQL variables.
if [[ "$DB_URL_FOUND" -eq 0 ]]; then
  if [[ -n "${PGHOST:-${POSTGRES_HOST:-}}" && -n "${PGUSER:-${POSTGRES_USER:-}}" && ( -n "${PGPASSWORD:-${POSTGRES_PASSWORD:-}}" ) && -n "${PGDATABASE:-${POSTGRES_DB:-}}" ]]; then
    DB_URL_FOUND=1
  fi
fi

if [[ "$DB_URL_FOUND" -eq 1 ]]; then
  echo "Database configuration detected. Initializing missing tables..."
  flask --app app init-db
else
  echo "ERROR: No PostgreSQL variables are visible in this Web service."
  echo "Add a Railway Variable Reference from the Postgres service to this Web service."
  echo "Expected DATABASE_URL (recommended), another supported URL alias, or complete PG* variables."
  echo "Starting diagnostic page only; no SQLite database will be used for records."
fi

exec gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 2 --timeout 120 app:app
