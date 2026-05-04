#!/bin/bash
# Start the Data Pipeline local PostgreSQL container via Podman.
# Run from the repo root: ./scripts/start-datapipeline-dev.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INIT_SQL="$REPO_ROOT/app/datapipeline/db/init.sql"
CONTAINER="${DATAPIPELINE_CONTAINER:-aiadocuments}"

# Load .env so DB_* vars are available
if [ -f "$REPO_ROOT/.env" ]; then
  set -a; source "$REPO_ROOT/.env"; set +a
fi

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-aiadocuments}"
DB_USER="${DB_USER:-aiauser}"
DB_PASSWORD="${DB_PASSWORD:-Admin123$}"

# Remove stopped container with the same name so re-runs are idempotent
if podman container exists "$CONTAINER" 2>/dev/null; then
  echo "Removing existing container: $CONTAINER"
  podman rm -f "$CONTAINER"
fi

echo "Starting PostgreSQL container: $CONTAINER"
podman run -d \
  --name "$CONTAINER" \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASSWORD" \
  -p "${DB_PORT}:5432" \
  -v "$INIT_SQL:/docker-entrypoint-initdb.d/01_init.sql:ro,z" \
  postgres:16-alpine

echo "Waiting for PostgreSQL to be ready..."
until podman exec "$CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" > /dev/null 2>&1; do
  sleep 1
done

echo ""
echo "PostgreSQL is ready."
echo "  Host:     $DB_HOST"
echo "  Port:     $DB_PORT"
echo "  Database: $DB_NAME"
echo "  User:     $DB_USER"
echo ""
echo "Connect:  psql postgresql://$DB_USER:***@$DB_HOST:$DB_PORT/$DB_NAME"
echo "Stop:     podman stop $CONTAINER"
echo "Remove:   podman rm -f $CONTAINER"
echo "Logs:     podman logs $CONTAINER"
