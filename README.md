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

    TaskQueue -->|Consume| RelayService[Relay Service]
    RelayService -->|Publish StatusMessage| StatusQueue[SQS: aia-status]

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
│   ├── task_message.py       # SQS message published to aia-tasks
│   ├── status_message.py     # SQS message received from aia-status
│   ├── orchestrate_request.py# Payload for POST /orchestrate
│   └── document_record.py
├── repositories/
│   ├── document_repository.py  # document_uploads table queries
│   └── user_repository.py      # users table queries + guest fallback
├── services/
│   ├── upload_service.py       # Upload, status, history, result
│   ├── orchestrator_service.py # Fire-and-forget HTTP client → Orchestrator
│   ├── ingestor_service.py     # DOCX text extraction
│   ├── s3_service.py           # S3 upload/download
│   └── sqs_service.py          # send_task, receive_messages, delete_message
├── orchestrator/
│   ├── main.py     # FastAPI service :8001 — POST /orchestrate + status queue poller
│   ├── session.py  # In-memory per-document agent dispatch state
│   └── summary.py  # SummaryGenerator protocol + MarkdownSummaryGenerator
├── relay_service/
│   ├── __init__.py
│   ├── main.py     # FastAPI app :8002 — lifespan starts SQS polling loop + /health
│   └── worker.py   # run_worker() polling loop + dispatch() — bridges TaskMessage → AgentResult → StatusMessage
└── utils/
    ├── postgres.py   # Connection pool, schema init (document_uploads + users tables)
    ├── auth.py       # JWT validation (HS256)
    ├── app_context.py
    └── logger.py
scripts/
├── mock_agent.py           # Test harness — simulates Relay Service (left-hand side)
├── mock_orchestrator.py    # Test harness — simulates CoreBackend + Orchestrator (right-hand side)
├── start-localstack.sh
├── start-datapipeline-dev.sh
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
├── test_orchestrator_session.py        # SessionStore — create, record, remove, events
├── test_orchestrator_summary.py        # MarkdownSummaryGenerator — all result formats
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
| `TASK_QUEUE_URL` | SQS queue consumed by Relay Service | `…/aia-tasks` |
| `STATUS_QUEUE_URL` | SQS queue polled by Orchestrator | `…/aia-status` |
| `ORCHESTRATOR_URL` | Orchestrator base URL (called by CoreBackend) | `http://localhost:8001` |
| `ORCHESTRATOR_PORT` | Port the Orchestrator listens on | `8001` |
| `ORCHESTRATOR_AGENT_TIMEOUT_SECONDS` | Max wait for agent responses | `480` |
| `ORCHESTRATOR_DEFAULT_AGENT_TYPE` | Fallback agent type for unknown templates | `general` |

## Running in Development

### 1. Start local infrastructure (PostgreSQL + LocalStack)
```bash
docker compose up -d
```

Docker Compose starts PostgreSQL and LocalStack. LocalStack initialises the `aia-tasks` and `aia-status` SQS queues and the `docsupload` S3 bucket automatically.

### 2. Start CoreBackend (port 8086)
```bash
uvicorn app.api.main:app --host 127.0.0.1 --port 8086 --reload
```

### 3. Start the Orchestrator (port 8001, separate terminal)
```bash
uvicorn app.orchestrator.main:app --host 127.0.0.1 --port 8001 --reload
```

Swagger UI:
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
| `test_orchestrator_summary.py` | `MarkdownSummaryGenerator` — all result shapes and formatting | No |
| `test_orchestrator_processing.py` | `POST /orchestrate` endpoint + `_process_document` (COMPLETE, PARTIAL_COMPLETE, ERROR paths) | No (mocked) |
| `test_upload_router.py` | CoreBackend upload/history/result endpoints | No (mocked) |
| `test_document_repository.py` | `DocumentRepository` DB queries | No (mocked) |
| `test_ingestor_service.py` | DOCX text extraction | No |
| `test_sqs_service.py` | SQS send/receive/delete | No (mocked) |
| `test_relay_service.py` | Relay Service `dispatch()`, `_get_document()`, `run_worker()` polling loop | No (mocked) |

> Orchestrator and Relay Service tests use `pytest-asyncio`. This is included in `requirements-dev.txt`.

## Pipeline Component Testing

Two standalone scripts in `scripts/` let you test either half of the pipeline in isolation — without needing the full system running.

```
┌──────────────────────────────────────────────────────────────────────┐
│  CoreBackend + Orchestrator  ←─── aia-tasks ───→  Relay Service       │
│                              ───→ aia-status ───→                    │
│                                                                       │
│  mock_orchestrator.py        ←─── aia-tasks ───→  Relay Service       │
│  (replaces left side)        ───→ aia-status ───→                    │
│                                                                       │
│  CoreBackend + Orchestrator  ←─── aia-tasks ───→  mock_agent.py      │
│                              ───→ aia-status ───→  (replaces right)  │
└──────────────────────────────────────────────────────────────────────┘
```

### `scripts/mock_agent.py` — simulate the Relay Service

**Purpose:** Tests **CoreBackend + Orchestrator** in isolation. Run this instead of the real Relay Service when you want to verify that the upload flow, SQS publishing, result persistence, and status transitions all work correctly without invoking Claude.

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

The fabricated result matches the `AgentResult` shape expected by the Orchestrator — it includes `assessments`, `metadata`, and `final_summary` fields so the summary generator and DB write proceed normally.

---

### `scripts/mock_orchestrator.py` — simulate CoreBackend + Orchestrator

**Purpose:** Tests the **Relay Service** in isolation. Run this instead of the full backend when you want to verify that the Relay Service correctly picks up tasks, calls the evaluation pipeline (or its dependencies), and returns well-formed `StatusMessage`s.

The script pushes one `TaskMessage` per agent type to `aia-tasks`, then polls `aia-status` until all responses arrive or the timeout expires, and prints a formatted result summary.

```bash
# Push security + technical tasks for a random document ID, wait up to 180 s
python scripts/mock_orchestrator.py

# Fix the document ID (useful for repeatability)
python scripts/mock_orchestrator.py --doc-id my-doc-001

# Only push a task for the security agent
python scripts/mock_orchestrator.py --agent-types security

# Push tasks for all three SDA agents
python scripts/mock_orchestrator.py --agent-types security technical both

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
    "SDA": ["security", "data", "risk", "ea", "solution"],
    "CHEDP": ["security", "data", "risk"],
}
```

To add a new template, add an entry to `TEMPLATE_AGENTS` and redeploy. If a `templateType` has no entry, the Orchestrator falls back to a single task using `ORCHESTRATOR_DEFAULT_AGENT_TYPE` (default: `general`).

Each agent type in the list becomes one `TaskMessage` on the `aia-tasks` queue. The Orchestrator waits for all of them to respond before writing a terminal status. See [docs/adr-orchestrator-fan-out.md](docs/adr-orchestrator-fan-out.md) for the full design decision.

## Verification and Debugging

### Check document status in the database
```bash
docker exec -it aia-backend-db-1 psql -U aiauser -d aia_documents

-- Query document lifecycle
SELECT doc_id, file_name, status, uploaded_ts, processed_ts
FROM document_uploads
ORDER BY uploaded_ts DESC;

-- Check users table
SELECT * FROM users;
```

### Check S3 uploads
```bash
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=eu-west-2 \
aws s3 ls s3://docsupload --endpoint-url http://localhost:4566 --recursive
```

### Check SQS queues
```bash
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=eu-west-2 \
aws sqs get-queue-attributes \
  --queue-url http://localhost:4566/000000000000/aia-tasks \
  --endpoint-url http://localhost:4566 \
  --attribute-names ApproximateNumberOfMessages
```

### Reset local state
```bash
# Clear database
docker exec -it aia-backend-db-1 psql -U aiauser -d aia_documents \
  -c "TRUNCATE document_uploads; TRUNCATE users CASCADE;"

# Clear S3
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=eu-west-2 \
aws s3 rm s3://docsupload --recursive --endpoint-url http://localhost:4566
```

## Contributing

Format code and run tests before submitting a pull request. Keep routing logic in `api/`, business logic in `services/`, and data access in `repositories/`.
