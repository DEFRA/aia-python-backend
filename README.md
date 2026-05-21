# AIA Backend Service

The AIA Backend Service consists of two co-located processes — **CoreBackend** (HTTP API) and **Orchestrator** (assessment pipeline) — that together handle secure document uploads, JWT-based authentication, specialist agent dispatch via SQS, and result persistence to PostgreSQL.

## Core Functionality

The service exposes the following API endpoints under the base path `/api/v1`:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (no auth) |
| POST | `/api/v1/documents/upload` | Upload a document for AI assessment |
| GET | `/api/v1/documents/status` | List document IDs still in PROCESSING for the user |
| GET | `/api/v1/documents` | Paginated upload history |
| GET | `/api/v1/documents/{documentId}` | Full result including `resultMd` |
| GET | `/api/v1/users/me` | Authenticated user profile |
| GET | `/api/v1/cost-usage` | Paginated per-document token / cost usage with summary totals |
| GET | `/api/v1/cost-usage/{documentId}` | Token / cost usage for a single document |

Full request/response contracts are documented in [docs/corebackend-api.md](docs/corebackend-api.md).

## Document Status Lifecycle

```
PROCESSING  →  COMPLETE
     ↓              ↑
     ↓         PARTIAL_COMPLETE  (≥1 agent responded within timeout)
     ↓
   ERROR
```

| Status | Terminal | Meaning |
|--------|----------|---------|
| `PROCESSING` | No | Upload received; AI assessment in progress |
| `COMPLETE` | Yes | All agents responded; `resultMd` populated |
| `PARTIAL_COMPLETE` | Yes | Timeout reached with partial results; `resultMd` + `errorMessage` (lists non-responding agents) populated |
| `ERROR` | Yes | Unrecoverable failure or zero agent responses; `errorMessage` populated |

## Architecture

```mermaid
graph TD
    Client[Frontend Client] -->|POST /documents/upload| Auth[Auth Layer / JWT Validation]
    Auth --> CB[CoreBackend :8086]

    CB -->|1. Insert metadata status=PROCESSING| DB[(PostgreSQL RDS)]
    CB -->|2. Upload binary async| S3[AWS S3]
    CB -->|3. POST /orchestrate fire-and-forget| ORC[Orchestrator :8001]

    ORC -->|Download file| S3
    ORC -->|Publish TaskMessage| TaskQueue[SQS: aia-tasks]
    ORC -->|UPDATE status=PROCESSING| DB

    TaskQueue -->|Consume| AgentService[Agent Service]
    AgentService -->|Publish StatusMessage| StatusQueue[SQS: aia-status]

    StatusQueue -->|Poll| ORC
    ORC -->|UPDATE status=COMPLETE / PARTIAL_COMPLETE / ERROR + resultMd| DB

    Client -->|GET /documents/status| CB
    CB -->|Read PROCESSING rows| DB
```

Once CoreBackend accepts the upload it inserts a DB record, uploads the file to S3, then fires `POST /orchestrate` to the Orchestrator (same ECS task, `localhost:8001`). The Orchestrator extracts the document text, publishes a `TaskMessage` to **aia-tasks**, and waits for results on **aia-status** with a configurable timeout (default 8 minutes). On completion it writes `resultMd` and the terminal status to the database. The frontend polls `GET /documents/status` until the document ID disappears from the list, then fetches the full result.

## Project Structure

```
app/
├── api/
│   ├── main.py               # FastAPI app, router registration, lifespan
│   ├── documents.py          # /documents/* endpoints
│   ├── users.py              # /users/me endpoint
│   ├── cost_usage.py         # /cost-usage list + per-document endpoints
│   └── health.py             # /health endpoint
├── core/
│   ├── config.py             # Pydantic settings (env vars → typed config) + TEMPLATE_AGENTS mapping
│   ├── dependencies.py       # FastAPI DI providers
│   ├── enums.py              # DocumentStatus (PROCESSING, COMPLETE, PARTIAL_COMPLETE, ERROR)
│   └── messages.py           # User-facing error strings
├── models/
│   ├── upload_request.py
│   ├── upload_response.py    # { documentId, status }
│   ├── history_record.py     # { documentId, originalFilename, templateType, status, ... }
│   ├── result_record.py      # { ..., resultMd, errorMessage }
│   ├── user_record.py        # { userId, email, name }
│   ├── cost_usage_record.py  # CostUsageDocument, CostUsageResponse, Pagination, CostUsageSummary
│   ├── task_message.py       # Shared with Agent Service scripts/tests
│   ├── status_message.py     # Shared with Agent Service scripts/tests
│   ├── orchestrate_request.py# Shared schema (Orchestrator uses local copy under src/schemas)
│   └── document_record.py
├── repositories/
│   ├── document_repository.py  # document_uploads table queries
│   ├── user_repository.py      # users table queries + guest fallback
│   └── cost_usage_repository.py# Per-user / per-document cost_usage joins
├── services/
│   ├── upload_service.py       # Upload, status, history, result
│   ├── orchestrator_service.py # Fire-and-forget HTTP client → Orchestrator
│   ├── ingestor_service.py     # DOCX text extraction
│   ├── cost_usage_service.py   # Group rows → documents, build summary + pagination
│   ├── s3_service.py           # S3 upload/download
│   └── sqs_service.py          # send_task, receive_messages, delete_message
├── orchestrator/
│   ├── tests/
│   └── src/
│       ├── main.py              # FastAPI service :8001 — POST /orchestrate + status queue poller
│       ├── config/
│       │   └── pipeline_config.py
│       ├── repositories/
│       │   ├── document_repository.py
│       │   └── cost_usage_repository.py
│       ├── services/
│       │   ├── s3_service.py    # Download-only for orchestration
│       │   └── sqs_service.py   # send_task, receive_messages, delete_message
│       ├── session/
│       │   └── store.py
│       ├── reporting/
│       │   └── report_generator.py
│       ├── schemas/
│       │   ├── contracts.py
│       │   ├── orchestrate_request.py
│       │   ├── status_message.py
│       │   └── task_message.py
│       └── utils/
│           ├── app_context.py
│           ├── document_parser.py
│           ├── enums.py
│           ├── logger.py
│           └── postgres.py
├── agent_service/
│   ├── __init__.py
│   ├── main.py     # FastAPI app :8002 — lifespan starts SQS polling loop + /health
│   └── worker.py   # run_worker() concurrent polling loop — asyncio.create_task per message, Semaphore-bounded
└── utils/
    ├── postgres.py   # Connection pool, schema init (document_uploads + users tables)
    ├── auth.py       # JWT validation (HS256)
    ├── app_context.py
    └── logger.py
app/scripts/
└── seed_cost_usage.sql       # Seed four sample documents + cost-usage rows for /cost-usage testing
scripts/
├── start-aia.sh              # Connectivity checks + start all three services
├── start-datapipeline-dev.sh # Start Podman PostgreSQL container
├── mock_agent.py             # Test harness — simulates Agent Service (right side)
├── mock_orchestrator.py      # Test harness — simulates CoreBackend + Orchestrator (left side)
├── start-localstack.sh
└── start_dev_server.sh
docs/
├── corebackend-api.md                  # Full API reference for frontend integration
├── orchestrator-api.md                 # Orchestrator internal service reference
├── db-schema.md                        # PostgreSQL schema, ERD, and column descriptions
├── adr-orchestrator-session-storage.md # ADR: in-memory vs persistent session state
└── adr-orchestrator-fan-out.md         # ADR: dynamic agent fan-out strategy
tests/
├── test_document_repository.py         # DocumentRepository DB queries
├── test_ingestor_service.py            # DOCX text extraction
├── test_sqs_service.py                 # SQS send/receive/delete
├── test_upload_router.py               # CoreBackend upload/history/result endpoints
├── test_cost_usage_repository.py       # CostUsageRepository — upsert query shape
├── test_cost_usage_service.py          # Grouping, per-doc totalCost, summary, pagination
├── test_cost_usage_router.py           # /cost-usage endpoints — auth, validation, 404
├── test_orchestrator_session.py        # SessionStore — create, record, remove, events
├── test_orchestrator_summary.py        # MarkdownReportGenerator — all result formats
└── test_orchestrator_processing.py     # POST /orchestrate + _process_document pipeline
```

## Setup and Installation

### 1. Clone the repository
```bash
git clone <repository-url>
cd aia-backend
```

### 2. Create and activate a virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
# Evaluation pipeline extras (agents, PDF parsing, report generation)
pip install -r app/agents/evaluation/requirements.txt
```

### 4. Configure environment variables
```bash
cp .env.example .env
# Edit .env — key values below
```

Key environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `JWT_SECRET` | HS256 signing secret | — |
| `POSTGRES_URI` | PostgreSQL connection string | — |
| `S3_BUCKET_NAME` | S3 bucket for document storage | `docsupload` |
| `TASK_QUEUE_URL` | SQS queue consumed by Agent Service | `…/aia-tasks` |
| `STATUS_QUEUE_URL` | SQS queue polled by Orchestrator | `…/aia-status` |
| `ORCHESTRATOR_URL` | Orchestrator base URL (called by CoreBackend) | `http://localhost:8001` |
| `ORCHESTRATOR_PORT` | Port the Orchestrator listens on | `8001` |
| `AGENT_TIMEOUT_SECONDS` | Max wait for agent responses | `480` |
| `ORCHESTRATOR_DEFAULT_AGENT_TYPE` | Fallback agent type for unknown templates | `general` |
| `LLM_PROVIDER` | LLM backend — `bedrock` (AWS) or `anthropic` (direct API) | `bedrock` |
| `AWS_ACCESS_KEY_ID` | AWS access key (STS short-lived credentials supported) | — |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | — |
| `AWS_SESSION_TOKEN` | STS session token (required for temporary credentials) | — |
| `AWS_DEFAULT_REGION` | AWS region for S3, SQS, and Bedrock | `eu-west-2` |

### Token Pricing Configuration (Orchestrator)

The Orchestrator calculates `total_cost_usd` from token usage using model-specific pricing in USD per 1M tokens.

Source of truth in code:
- `app/config.py` → `DEFAULT_LLM_PRICING_USD_PER_MTOKENS`
- `app/orchestrator/src/main.py` → `_calculate_total_cost_usd(...)`

You can override pricing via `.env` using `LLM_PRICING_USD_PER_MTOKENS` as JSON:

```env
LLM_PRICING_USD_PER_MTOKENS={"anthropic.claude-3-5-sonnet-20241022-v2:0":{"input":3.0,"output":15.0},"anthropic.claude-3-5-haiku-20241022-v1:0":{"input":0.8,"output":4.0}}
```

Example with Anthropic direct model IDs:

```env
LLM_PRICING_USD_PER_MTOKENS={"claude-3-5-sonnet-20241022":{"input":3.0,"output":15.0},"claude-3-5-haiku-20241022":{"input":0.8,"output":4.0}}
```

Cost formula used by Orchestrator:

```text
total_cost_usd = round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 6)
```

If a status message includes an unknown model ID, Orchestrator logs a warning and persists `total_cost_usd = 0.0` for that record.

The evaluation pipeline also reads `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` (individual vars) in addition to `POSTGRES_URI`.

## Running in Development

### 1. Start the PostgreSQL container (Podman)

```bash
./scripts/start-datapipeline-dev.sh
```

This starts a Podman-managed PostgreSQL container named `aiadocuments` on port 5432, applies `app/datapipeline/db/init.sql` (schema + seed data), and waits until ready.  S3, SQS, and Bedrock run on real AWS — no LocalStack required.

### 2. Start all three services with one command

```bash
./scripts/start-aia.sh
```

The script:
1. Verifies connectivity to PostgreSQL, S3, SQS (both queues), and Bedrock before starting anything.
2. Starts CoreBackend (`:8086`), Orchestrator (`:8001`), and Agent Service (`:8002`) as background processes with individual log files under `logs/`.
3. Confirms each service is still alive 2 seconds after launch and prints the health check URLs.

```
  Core Backend   →  http://127.0.0.1:8086/health
  Orchestrator   →  http://127.0.0.1:8001
  Agent Service  →  http://127.0.0.1:8002/health
```

Other modes:

```bash
./scripts/start-aia.sh --check   # connectivity checks only — no services started
./scripts/start-aia.sh --logs    # tail all three log files (Ctrl-C to stop)
./scripts/start-aia.sh --stop    # gracefully stop all running services
```

> **STS credentials** expire every few hours. Refresh `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` in `.env`, then re-run `./scripts/start-aia.sh --check` to confirm before starting services.

### Seed sample cost-usage data

For exercising `/api/v1/cost-usage` from Postman or the frontend without running full assessments, an idempotent seed loads four sample documents (Security / Technology / Architecture / Governance agent breakdowns) under the guest user:

```bash
podman exec -i aiadocuments psql -U aiauser -d aiadocuments < app/scripts/seed_cost_usage.sql
```

The script clears any existing cost rows for these four file names before re-insertion, so repeated runs leave a clean fixture.

### Swagger UI

- CoreBackend — `http://127.0.0.1:8086/docs`
- Orchestrator — `http://127.0.0.1:8001/docs`

## Running Tests

Install dev dependencies first if you haven't already:

```bash
pip install -r requirements-dev.txt
```

Run the full test suite:

```bash
PYTHONPATH=. pytest tests/
```

Run with coverage report:

```bash
PYTHONPATH=. pytest tests/ --cov=app --cov-report=term-missing
```

Run a specific module:

```bash
PYTHONPATH=. pytest tests/test_orchestrator_session.py -v
PYTHONPATH=. pytest tests/test_orchestrator_summary.py -v
PYTHONPATH=. pytest tests/test_orchestrator_processing.py -v
```

**Test categories:**

| File | What it covers | Needs infrastructure |
|------|---------------|----------------------|
| `test_orchestrator_session.py` | `SessionStore` — create, record results, completion event, remove | No |
| `test_orchestrator_summary.py` | `MarkdownReportGenerator` — per-category tables, scorecard, priority actions | No |
| `test_orchestrator_processing.py` | `POST /orchestrate` endpoint + `_process_document` (COMPLETE, PARTIAL_COMPLETE, ERROR paths) | No (mocked) |
| `test_upload_router.py` | CoreBackend upload/history/result endpoints | No (mocked) |
| `test_document_repository.py` | `DocumentRepository` DB queries | No (mocked) |
| `test_ingestor_service.py` | DOCX text extraction | No |
| `test_sqs_service.py` | SQS send/receive/delete | No (mocked) |
| `test_agent_service.py` | Agent Service `_process_message()`, `_get_document()`, concurrent `run_worker()` loop | No (mocked) |
| `test_cost_usage_repository.py` | `CostUsageRepository` upsert query shape and parameter binding | No (mocked) |
| `test_cost_usage_service.py` | Document grouping, per-doc `totalCost = SUM(total_cost_usd)`, summary aggregation, document-level pagination | No |
| `test_cost_usage_router.py` | `/cost-usage` and `/cost-usage/{id}` — auth, query-param validation, 404 path | No (mocked) |

> Orchestrator and Agent Service tests use `pytest-asyncio`. This is included in `requirements-dev.txt`.

## Pipeline Component Testing

Two standalone scripts in `scripts/` let you test either half of the pipeline in isolation — without needing the full system running.

```
┌──────────────────────────────────────────────────────────────────────┐
│  CoreBackend + Orchestrator  ───→ aia-tasks  ───→  Agent Service     │
│                              ←─── aia-status ←───                    │
│                                                                      │
│  mock_orchestrator.py        ───→ aia-tasks  ───→  Agent Service     │
│  (replaces left side)        ←─── aia-status ←───                    │
│                                                                      │
│  CoreBackend + Orchestrator  ───→ aia-tasks ───→  mock_agent.py      │
│                              ←─── aia-status ←───  (replaces right)  │
└──────────────────────────────────────────────────────────────────────┘
```

### `scripts/mock_agent.py` — simulate the Agent Service

**Purpose:** Tests **CoreBackend + Orchestrator** in isolation. Run this instead of the real Agent Service when you want to verify that the upload flow, SQS publishing, result persistence, and status transitions all work correctly without invoking Claude.

The script polls `aia-tasks`, generates a fabricated `StatusMessage` for every `TaskMessage` it receives, and immediately pushes the response to `aia-status`.

```bash
# Run indefinitely, return a random rating (Green / Amber / Red) per task
python scripts/mock_agent.py

# Stop after 3 tasks, always return Amber
python scripts/mock_agent.py --count 3 --rating Amber

# Simulate a slow agent (2 s response delay)
python scripts/mock_agent.py --delay 2

# Combine: process 5 tasks, force Red, 1 s delay
python scripts/mock_agent.py --count 5 --rating Red --delay 1
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--count` | int | run forever | Stop after N tasks |
| `--rating` | `Green` \| `Amber` \| `Red` | random | Rating included in every mock assessment |
| `--delay` | float (seconds) | 0 | Wait before publishing the response (simulates LLM latency) |

The fabricated result matches the `AgentResult` shape expected by the Orchestrator — it includes `assessments` (list of `AssessmentRow` with `question_id`, `Rating`, `Comments`, `Reference`), `summary`, `policy_doc_filename`, and `policy_doc_url` fields so the summary generator and DB write proceed normally.

---

### `scripts/mock_orchestrator.py` — simulate CoreBackend + Orchestrator

**Purpose:** Tests the **Agent Service** in isolation. Run this instead of the full backend when you want to verify that the Agent Service correctly picks up tasks, calls the evaluation pipeline (or its dependencies), and returns well-formed `StatusMessage`s.

The script pushes one `TaskMessage` per agent type to `aia-tasks`, then polls `aia-status` until all responses arrive or the timeout expires, and prints a formatted result summary.

```bash
# Push security + technical tasks for a random document ID, wait up to 180 s
python scripts/mock_orchestrator.py

# Fix the document ID (useful for repeatability)
python scripts/mock_orchestrator.py --doc-id my-doc-001

# Only push a task for the security agent
python scripts/mock_orchestrator.py --agent-types security

# Push tasks for both SDA agents
python scripts/mock_orchestrator.py --agent-types security technical

# Send a real file as the document content
python scripts/mock_orchestrator.py --file app/agents/evaluation/files/security_policy.md

# Extend the timeout for slow LLM calls
python scripts/mock_orchestrator.py --timeout 300
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--doc-id` | string | random UUID | Document ID stamped on every task |
| `--agent-types` | list of strings | `security technical` | Agent types to dispatch |
| `--timeout` | float (seconds) | 180 | How long to wait for all status responses |
| `--file` | path | built-in mock text | File whose content is sent as `fileContent` |

The script prints each received `StatusMessage` including ratings, assessment counts, and the final interpretation. Missing responses (timeout) are flagged at the end.

---

> Both scripts read credentials and queue URLs from the root `.env` file (the same `AppConfig` used by the application), so they work with LocalStack and real AWS transparently.

## Template Configuration

The Orchestrator fans out to specialist agents based on the document's `templateType`. The mapping lives in `app/core/config.py`:

```python
TEMPLATE_AGENTS: dict[str, list[str]] = {
    "SDA": ["security", "technical"],
    # "CHEDP": ["security", "data", "risk", "ea", "solution"],
}
```

To add a new template, add an entry to `TEMPLATE_AGENTS` and redeploy. If a `templateType` has no entry, the Orchestrator falls back to a single task using `ORCHESTRATOR_DEFAULT_AGENT_TYPE` (default: `general`).

Each agent type in the list becomes one `TaskMessage` on the `aia-tasks` queue. The Orchestrator waits for all of them to respond before writing a terminal status. See [docs/adr-orchestrator-fan-out.md](docs/adr-orchestrator-fan-out.md) for the full design decision.

## Verification and Debugging

### Run connectivity checks
```bash
./scripts/start-aia.sh --check
```

Reports PostgreSQL (active question count), S3 (bucket access), SQS tasks queue depth, SQS status queue depth, and a 1-token Bedrock probe call.

### Check document status in the database
```bash
# Connect via Podman container (DB_USER / DB_NAME from .env)
psql postgresql://aiauser:Admin123\$@localhost:5432/aiadocuments

-- Document lifecycle
SELECT doc_id, file_name, status, uploaded_ts, processed_ts
FROM document_uploads
ORDER BY uploaded_ts DESC;

-- Active questions loaded by data pipeline
SELECT COUNT(*) FROM data_pipeline.questions WHERE isactive = TRUE;
```

### Check S3 uploads
```bash
# Requires AWS CLI with credentials from .env (or exported in the shell)
aws s3 ls s3://<S3_BUCKET_NAME> --recursive
```

### Check SQS queue depth
```bash
aws sqs get-queue-attributes \
  --queue-url "$TASK_QUEUE_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

### Follow service logs
```bash
./scripts/start-aia.sh --logs      # tail all three at once
tail -f logs/agent-service.log     # single service
```

### Reset local state between test runs
```bash
# Truncate document table so the same file can be re-uploaded
psql postgresql://aiauser:Admin123\$@localhost:5432/aiadocuments \
  -c "TRUNCATE document_uploads CASCADE;"

# Purge leftover SQS messages (if a run left tasks behind)
aws sqs purge-queue --queue-url "$TASK_QUEUE_URL"
aws sqs purge-queue --queue-url "$STATUS_QUEUE_URL"
```

## Contributing

Format code and run tests before submitting a pull request. Keep routing logic in `api/`, business logic in `services/`, and data access in `repositories/`.
