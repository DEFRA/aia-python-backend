#!/usr/bin/env bash
# scripts/start-aia.sh
#
# Verify S3 / SQS / Bedrock / PostgreSQL connectivity, then start all three
# AIA backend services as background processes with per-service log files.
#
# Usage (run from repo root):
#   ./scripts/start-aia.sh            # checks + start all services
#   ./scripts/start-aia.sh --check    # connectivity checks only, no start
#   ./scripts/start-aia.sh --stop     # stop services from a previous run
#   ./scripts/start-aia.sh --logs     # tail all three service logs (Ctrl-C to exit)
#
# Requirements:
#   • .env in the repo root with AWS_*, S3_BUCKET_NAME, TASK/STATUS_QUEUE_URL,
#     POSTGRES_URI (or DB_* vars), and LLM_PROVIDER=bedrock
#   • .venv built from requirements.txt
#   • Podman PostgreSQL container running
#     (start with: ./scripts/start-datapipeline-dev.sh)
#
# STS credentials expire every few hours.  Re-run after refreshing .env.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
VENV_UVICORN="$REPO_ROOT/.venv/bin/uvicorn"
LOG_DIR="$REPO_ROOT/logs"
PID_FILE="$REPO_ROOT/.aia.pids"

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

ok()     { printf "${GREEN}  ✓${NC}  %-28s %s\n" "$1" "${2:-}"; }
fail()   { printf "${RED}  ✗${NC}  %-28s %s\n" "$1" "${2:-}"; }
warn()   { printf "${YELLOW}  !${NC}  %-28s %s\n" "$1" "${2:-}"; }
info()   { printf "     %-28s %s\n" "$1" "${2:-}"; }
banner() { echo -e "\n${BOLD}$1${NC}"; printf '─%.0s' {1..58}; echo; }

# ── Check .venv up-front (needed for safe .env loading below) ─────────────────
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo -e "${RED}ERROR:${NC} .venv not found at $VENV_PYTHON"
    echo "  Build it:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# ── Load .env via python-dotenv ───────────────────────────────────────────────
# Using Python avoids bash variable expansion on values that contain special
# characters such as $ in passwords (e.g. Admin123$@...).
if [ -f "$REPO_ROOT/.env" ]; then
    eval "$("$VENV_PYTHON" -c "
import shlex, sys
from dotenv import dotenv_values
for k, v in dotenv_values('$REPO_ROOT/.env').items():
    if v is not None:
        print(f'export {k}={shlex.quote(v)}')
" 2>/dev/null)"
fi

# ── Parse argument ─────────────────────────────────────────────────────────────
MODE="${1:---start}"   # default to --start if no arg

# ── --logs mode ───────────────────────────────────────────────────────────────
if [[ "$MODE" == "--logs" ]]; then
    exec tail -f \
        "$LOG_DIR/core-backend.log" \
        "$LOG_DIR/orchestrator.log" \
        "$LOG_DIR/relay-service.log"
fi

# ── --stop mode ───────────────────────────────────────────────────────────────
if [[ "$MODE" == "--stop" ]]; then
    banner "Stopping AIA backend services"
    if [ ! -f "$PID_FILE" ]; then
        warn "No PID file found" "nothing to stop"
        exit 0
    fi
    while IFS=: read -r name pid; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" && ok "$name" "PID $pid stopped"
        else
            warn "$name" "PID $pid already gone"
        fi
    done < "$PID_FILE"
    rm -f "$PID_FILE"
    echo ""
    ok "Done" ""
    exit 0
fi

# ── Change to repo root so Python module paths resolve ────────────────────────
cd "$REPO_ROOT"

# ─────────────────────────────────────────────────────────────────────────────
banner "Connectivity checks"
CHECKS_OK=true

# ── PostgreSQL ─────────────────────────────────────────────────────────────────
PG_OUT=$(
    "$VENV_PYTHON" - <<'PY' 2>&1 || true
import asyncio, asyncpg, os, sys

async def main():
    uri = os.environ.get("POSTGRES_URI") or (
        "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
            user=os.environ.get("DB_USER", "aiauser"),
            pw=os.environ.get("DB_PASSWORD", "Admin123$"),
            host=os.environ.get("DB_HOST", "localhost"),
            port=os.environ.get("DB_PORT", "5432"),
            db=os.environ.get("DB_NAME", "aiadocuments"),
        )
    )
    try:
        conn = await asyncpg.connect(uri, timeout=5)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS n FROM data_pipeline.questions WHERE isactive = TRUE"
    )
    await conn.close()
    print(f"connected — {row['n']} active questions in data_pipeline.questions")

asyncio.run(main())
PY
)
if echo "$PG_OUT" | grep -q "^connected"; then
    ok "PostgreSQL" "$PG_OUT"
else
    fail "PostgreSQL" "$PG_OUT"
    info "" "Start container:  ./scripts/start-datapipeline-dev.sh"
    CHECKS_OK=false
fi

# ── S3 ────────────────────────────────────────────────────────────────────────
S3_OUT=$(
    "$VENV_PYTHON" - <<'PY' 2>&1 || true
import boto3, os, sys
from botocore.exceptions import ClientError

bucket = os.environ.get("S3_BUCKET_NAME", "pocldnaia001")
region = os.environ.get("AWS_DEFAULT_REGION", "eu-west-2")
s3 = boto3.client(
    "s3",
    region_name=region,
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    aws_session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
)
try:
    s3.head_bucket(Bucket=bucket)
    print(f"accessible — s3://{bucket} in {region}")
except ClientError as exc:
    code = exc.response["Error"]["Code"]
    print(f"FAIL ({code}): {exc}", file=sys.stderr)
    sys.exit(1)
PY
)
if echo "$S3_OUT" | grep -q "^accessible"; then
    ok "S3" "$S3_OUT"
else
    fail "S3" "$S3_OUT"
    info "" "Check AWS credentials / bucket name in .env"
    CHECKS_OK=false
fi

# ── SQS — tasks queue ─────────────────────────────────────────────────────────
SQS_TASKS_OUT=$(
    "$VENV_PYTHON" - <<'PY' 2>&1 || true
import boto3, os, sys
from botocore.exceptions import ClientError

url = os.environ.get("TASK_QUEUE_URL", "")
if not url:
    print("FAIL: TASK_QUEUE_URL not set", file=sys.stderr)
    sys.exit(1)

sqs = boto3.client(
    "sqs",
    region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-west-2"),
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    aws_session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
)
try:
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    visible = attrs.get("ApproximateNumberOfMessages", "?")
    inflight = attrs.get("ApproximateNumberOfMessagesNotVisible", "?")
    print(f"reachable — ~{visible} waiting, ~{inflight} in-flight")
except ClientError as exc:
    print(f"FAIL: {exc}", file=sys.stderr)
    sys.exit(1)
PY
)
if echo "$SQS_TASKS_OUT" | grep -q "^reachable"; then
    ok "SQS tasks queue" "$SQS_TASKS_OUT"
else
    fail "SQS tasks queue" "$SQS_TASKS_OUT"
    info "" "Check TASK_QUEUE_URL and credentials in .env"
    CHECKS_OK=false
fi

# ── SQS — status queue ────────────────────────────────────────────────────────
SQS_STATUS_OUT=$(
    "$VENV_PYTHON" - <<'PY' 2>&1 || true
import boto3, os, sys
from botocore.exceptions import ClientError

url = os.environ.get("STATUS_QUEUE_URL", "")
if not url:
    print("FAIL: STATUS_QUEUE_URL not set", file=sys.stderr)
    sys.exit(1)

sqs = boto3.client(
    "sqs",
    region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-west-2"),
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    aws_session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
)
try:
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    visible = attrs.get("ApproximateNumberOfMessages", "?")
    inflight = attrs.get("ApproximateNumberOfMessagesNotVisible", "?")
    print(f"reachable — ~{visible} waiting, ~{inflight} in-flight")
except ClientError as exc:
    print(f"FAIL: {exc}", file=sys.stderr)
    sys.exit(1)
PY
)
if echo "$SQS_STATUS_OUT" | grep -q "^reachable"; then
    ok "SQS status queue" "$SQS_STATUS_OUT"
else
    fail "SQS status queue" "$SQS_STATUS_OUT"
    info "" "Check STATUS_QUEUE_URL and credentials in .env"
    CHECKS_OK=false
fi

# ── Bedrock ───────────────────────────────────────────────────────────────────
# Uses Haiku 3 (cheapest, widely available) for the probe call.
# Confirms the full credential chain works for Bedrock runtime in eu-west-2.
BEDROCK_OUT=$(
    "$VENV_PYTHON" - <<'PY' 2>&1 || true
import anthropic, asyncio, os, sys

async def main():
    client = anthropic.AsyncAnthropicBedrock(
        aws_region=os.environ.get("AWS_DEFAULT_REGION", "eu-west-2"),
        aws_access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
    )
    try:
        msg = await client.messages.create(
            model="anthropic.claude-3-haiku-20240307-v1:0",
            max_tokens=1,
            messages=[{"role": "user", "content": "1"}],
        )
        print(f"OK — Bedrock runtime responding (stop_reason={msg.stop_reason})")
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

asyncio.run(main())
PY
)
if echo "$BEDROCK_OUT" | grep -q "^OK"; then
    ok "Bedrock" "$BEDROCK_OUT"
else
    fail "Bedrock" "$BEDROCK_OUT"
    info "" "Check AWS credentials, region, and Bedrock model access in .env"
    CHECKS_OK=false
fi

echo ""

# ── Abort if any check failed ─────────────────────────────────────────────────
if [[ "$CHECKS_OK" == false ]]; then
    echo -e "${RED}One or more checks failed.${NC} Fix the issues above, then re-run."
    echo ""
    echo "  Tip: STS credentials expire — refresh .env if AWS checks fail."
    exit 1
fi

ok "All checks passed" ""
echo ""

# ── Check-only mode exits here ────────────────────────────────────────────────
if [[ "$MODE" == "--check" ]]; then
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
banner "Starting AIA backend services"

mkdir -p "$LOG_DIR"

# Kill any stale processes from a previous run
for pattern in "app.api.main:app" "app.orchestrator.main:app" "app.relay_service.main:app"; do
    pkill -f "$pattern" 2>/dev/null || true
done
sleep 1

> "$PID_FILE"

# start_service <display-name> <uvicorn-module> <port> <logfile>
start_service() {
    local name="$1" module="$2" port="$3" logfile="$4"
    # Truncate the log so the new run starts clean
    > "$logfile"
    "$VENV_UVICORN" "$module" \
        --host 127.0.0.1 \
        --port "$port" \
        >> "$logfile" 2>&1 &
    local pid=$!
    echo "${name}:${pid}" >> "$PID_FILE"
    info "$name" "port=$port  PID=$pid  log=logs/$(basename "$logfile")"
}

start_service "core-backend"  "app.api.main:app"            8086 "$LOG_DIR/core-backend.log"
start_service "orchestrator"  "app.orchestrator.main:app"   8001 "$LOG_DIR/orchestrator.log"
start_service "relay-service" "app.relay_service.main:app"  8002 "$LOG_DIR/relay-service.log"

# ── Wait then verify all three survived startup ───────────────────────────────
sleep 2
echo ""
ALL_UP=true
while IFS=: read -r name pid; do
    if kill -0 "$pid" 2>/dev/null; then
        ok "$name" "PID $pid — running"
    else
        fail "$name" "PID $pid — exited at startup"
        info "" "Check logs/$name.log for the error"
        ALL_UP=false
    fi
done < "$PID_FILE"

echo ""

if [[ "$ALL_UP" == true ]]; then
    echo -e "${BOLD}All services running.${NC}"
    echo ""
    echo "  Core Backend   →  http://127.0.0.1:8086/health"
    echo "  Orchestrator   →  http://127.0.0.1:8001"
    echo "  Relay Service  →  http://127.0.0.1:8002/health"
    echo ""
    echo "  Follow logs:   ./scripts/start-aia.sh --logs"
    echo "  Stop all:      ./scripts/start-aia.sh --stop"
else
    echo -e "${RED}One or more services failed to start.${NC}"
    exit 1
fi
