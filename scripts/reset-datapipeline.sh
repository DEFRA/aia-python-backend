#!/usr/bin/env bash
# scripts/reset-datapipeline.sh
#
# Truncate the three mutable data-pipeline tables, confirm they are empty,
# then run the data-pipeline to re-populate them from source_policy_docs.
#
# Usage (run from repo root or anywhere):
#   ./scripts/reset-datapipeline.sh
#
# Requirements:
#   • .env in the repo root with DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
#   • .venv built from requirements.txt (needs python-dotenv)
#   • psql on PATH (comes with PostgreSQL client tools)
#
# After the script completes:
#   • data_pipeline.policy_document_sync  — 0 rows
#   • data_pipeline.questions             — 0 rows
#   • data_pipeline.policy_documents      — 0 rows
#   • data_pipeline.source_policy_docs    — unchanged (seed data)
#   • policy_documents + questions        — re-populated by the pipeline run

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

# ── Colours ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

ok()     { printf "${GREEN}  ✓${NC}  %-36s %s\n" "$1" "${2:-}"; }
fail()   { printf "${RED}  ✗${NC}  %-36s %s\n" "$1" "${2:-}"; }
warn()   { printf "${YELLOW}  !${NC}  %-36s %s\n" "$1" "${2:-}"; }
banner() { echo -e "\n${BOLD}$1${NC}"; printf '─%.0s' {1..58}; echo; }

# ── Require .venv (needed to load .env via python-dotenv) ──────────────────────
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo -e "${RED}ERROR:${NC} .venv not found at $VENV_PYTHON"
    echo "  Build it:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# ── Load .env via python-dotenv ────────────────────────────────────────────────
# Python shlex.quote handles special characters in passwords (e.g. Admin123$@).
if [[ -f "$REPO_ROOT/.env" ]]; then
    eval "$("$VENV_PYTHON" -c "
import shlex, sys
from dotenv import dotenv_values
for k, v in dotenv_values('$REPO_ROOT/.env').items():
    if v is not None:
        print(f'export {k}={shlex.quote(v)}')
" 2>/dev/null)"
else
    echo -e "${RED}ERROR:${NC} .env not found at $REPO_ROOT/.env"
    exit 1
fi

# ── Resolve DB variables (with safe defaults) ──────────────────────────────────
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-aiadocuments}"
DB_USER="${DB_USER:-aiauser}"
DB_PASSWORD="${DB_PASSWORD:-}"

# Convenience wrapper — avoids repeating the connection flags everywhere.
pg() {
    PGPASSWORD="$DB_PASSWORD" psql \
        -h "$DB_HOST" -p "$DB_PORT" \
        -U "$DB_USER" -d "$DB_NAME" \
        -v ON_ERROR_STOP=1 \
        "$@"
}

# row_count <schema.table>  →  prints the integer row count
row_count() {
    pg -t -A -c "SELECT COUNT(*) FROM $1;" 2>/dev/null | tr -d '[:space:]'
}

cd "$REPO_ROOT"

# ──────────────────────────────────────────────────────────────────────────────
banner "Step 1 — verify PostgreSQL connection"

if PGPASSWORD="$DB_PASSWORD" psql \
        -h "$DB_HOST" -p "$DB_PORT" \
        -U "$DB_USER" -d "$DB_NAME" \
        -c "SELECT 1;" > /dev/null 2>&1; then
    ok "PostgreSQL" "$DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
else
    fail "PostgreSQL" "cannot connect — check DB_* vars in .env"
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
banner "Step 2 — truncate mutable tables (CASCADE)"

# Truncate in dependency order: sync → questions → policy_documents.
# CASCADE covers any FK children we may have missed.
TRUNCATE_SQL="
TRUNCATE TABLE
    data_pipeline.policy_document_sync,
    data_pipeline.questions,
    data_pipeline.policy_documents
CASCADE;
"

if pg -c "$TRUNCATE_SQL" > /dev/null 2>&1; then
    ok "Truncated" "policy_document_sync, questions, policy_documents"
else
    fail "Truncate failed" "check psql output above"
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
banner "Step 3 — confirm row counts"

FAIL_COUNT=0

check_empty() {
    local table="$1"
    local n
    n="$(row_count "$table")"
    if [[ "$n" == "0" ]]; then
        ok "$table" "0 rows"
    else
        fail "$table" "$n rows (expected 0)"
        FAIL_COUNT=$(( FAIL_COUNT + 1 ))
    fi
}

check_empty "data_pipeline.policy_document_sync"
check_empty "data_pipeline.questions"
check_empty "data_pipeline.policy_documents"

# source_policy_docs must still have rows (it is the seed table — never truncated).
SEED_COUNT="$(row_count "data_pipeline.source_policy_docs")"
if [[ "$SEED_COUNT" -gt 0 ]]; then
    ok "data_pipeline.source_policy_docs" "$SEED_COUNT rows (seed intact)"
else
    warn "data_pipeline.source_policy_docs" "0 rows — seed table is empty; pipeline will produce no output"
fi

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo ""
    echo -e "${RED}Truncate verification failed for $FAIL_COUNT table(s).${NC} Aborting pipeline run."
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
banner "Step 4 — run data pipeline"

echo ""
echo "  $ .venv/bin/python -m app.datapipeline.src.main"
echo ""

"$VENV_PYTHON" -m app.datapipeline.src.main
PIPELINE_EXIT=$?

echo ""
if [[ "$PIPELINE_EXIT" -eq 0 ]]; then
    ok "Pipeline exited cleanly" "exit code 0"
else
    fail "Pipeline exited with errors" "exit code $PIPELINE_EXIT"
fi

# ──────────────────────────────────────────────────────────────────────────────
banner "Step 5 — final row counts"

PD_COUNT="$(row_count "data_pipeline.policy_documents")"
Q_COUNT="$(row_count "data_pipeline.questions")"
SYNC_COUNT="$(row_count "data_pipeline.policy_document_sync")"

if [[ "$PD_COUNT" -gt 0 ]]; then
    ok "policy_documents" "$PD_COUNT rows"
else
    warn "policy_documents" "0 rows — pipeline may not have produced output"
fi

if [[ "$Q_COUNT" -gt 0 ]]; then
    ok "questions" "$Q_COUNT rows"
else
    warn "questions" "0 rows — pipeline may not have produced output"
fi

ok "policy_document_sync" "$SYNC_COUNT rows"
ok "source_policy_docs"   "$SEED_COUNT rows (unchanged)"

echo ""
if [[ "$PIPELINE_EXIT" -eq 0 ]]; then
    echo -e "${BOLD}Reset complete.${NC}"
else
    echo -e "${YELLOW}Reset finished with pipeline errors.${NC} Check the output above."
    exit "$PIPELINE_EXIT"
fi
