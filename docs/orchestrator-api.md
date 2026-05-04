# AIA Orchestrator — Internal Service Reference

**Version:** 1.0 (POC)  
**Last Updated:** 2026-04-27  
**Audience:** Backend Development Team  
**Port:** `8001` (same ECS task as CoreBackend, reachable via `localhost`)

---

## Overview

The Orchestrator is a FastAPI service that runs alongside CoreBackend inside the same ECS task. It is responsible for the entire document assessment pipeline after the file has been stored in S3:

1. Receive a fire-and-forget trigger from CoreBackend
2. Download and extract text from the DOCX file in S3
3. Publish a `TaskMessage` to the **aia-tasks** SQS queue (consumed by the Relay Service)
4. Track agent responses via an in-memory session store
5. Compile per-agent results into a Markdown report
6. Write the final status and `result_md` to PostgreSQL

The Orchestrator has **no outbound HTTP calls to the frontend**. All status updates flow through PostgreSQL — CoreBackend reads from the database and serves the current state to the frontend on demand. See [How Final Status Reaches the Frontend](#how-final-status-reaches-the-frontend).

---

## HTTP API

### POST /orchestrate

Called by CoreBackend immediately after a successful S3 upload. CoreBackend does **not** wait for a response beyond the `202 Accepted` — processing continues asynchronously.

```
POST http://localhost:8001/orchestrate
Content-Type: application/json
```

**Request body:**

```json
{
  "document_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "s3_key":      "3fa85f64-5717-4562-b3fc-2c963f66afa6_architecture-v2.docx",
  "template_type": "SDA"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `document_id` | UUID string | Document identifier (matches `doc_id` in `document_uploads`) |
| `s3_key` | string | S3 object key — format `{documentId}_{fileName}` |
| `template_type` | string | Assessment template — passed to the Relay Service |

**Response `202`:**

```json
{ "status": "accepted" }
```

The Orchestrator returns `202` immediately and runs the processing pipeline as a FastAPI `BackgroundTask`.

**Error behaviour:**

If the Orchestrator is unreachable, CoreBackend logs a warning and continues — the document stays in `PROCESSING` until the stuck-document cleanup resets it. No upload failure is surfaced to the user because of Orchestrator unavailability.

---

## Processing Pipeline

```
POST /orchestrate received
        │
        ▼
1. UPDATE document_uploads SET status = 'PROCESSING'
        │
        ▼
2. Download DOCX from S3  ──── failure ──► ERROR
        │
        ▼
3. Extract text (paragraphs + tables)  ── failure ──► ERROR
        │
        ▼
4. Publish TaskMessage → SQS: aia-tasks
        │
        ▼
5. Create in-memory session  ─────────────────────────────────────────────┐
   expected_task_ids = { "{documentId}_{agentType}" }                     │
        │                                                                  │
        ▼                                                                  │
6. asyncio.wait_for(completion_event, timeout=AGENT_TIMEOUT_SECONDS)      │
        │                                                                  │
   ┌────┴────────────────────────────────────┐                            │
   │ Status queue poller receives results    │◄───────────────────────────┘
   │ → session.record_result(task_id, result)│
   │ → sets completion_event when all done  │
   └────────────────────────────────────────┘
        │
   ┌────┴──────────────────────────────────────┐
   │ all results?  │  partial?  │  0 results?  │
   │   COMPLETE    │PARTIAL_COMPL│    ERROR     │
   └───────────────────────────────────────────┘
        │
        ▼
7. MarkdownSummaryGenerator.generate(collected_results) → result_md
        │
        ▼
8. UPDATE document_uploads SET status, result_md, error_message
```

---

## SQS Message Schemas

### TaskMessage — published to `aia-tasks`

Serialised as camelCase JSON.

```json
{
  "taskId":       "3fa85f64-5717-4562-b3fc-2c963f66afa6_security",
  "documentId":   "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "agentType":    "security",
  "templateType": "SDA",
  "fileContent":  "Full extracted text... (null if > 200 KB)",
  "s3Bucket":     "docsupload",
  "s3Key":        "3fa85f64-5717-4562-b3fc-2c963f66afa6_architecture-v2.docx"
}
```

| Field | Notes |
|-------|-------|
| `taskId` | Deterministic: `{documentId}_{agentType}` |
| `fileContent` | Inline text when `≤ 200 KB`; `null` when larger — agent falls back to `s3Key` |
| `agentType` | Currently driven by `ORCHESTRATOR_DEFAULT_AGENT_TYPE` config; fan-out to multiple agent types is pending |

### StatusMessage — received from `aia-status`

Published by the Relay Service after processing. Parsed as camelCase JSON.

```json
{
  "taskId":     "3fa85f64-5717-4562-b3fc-2c963f66afa6_security",
  "documentId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "agentType":  "security",
  "result":     { "score": 85, "findings": ["..."] },
  "error":      null
}
```

---

## In-Memory Session State

Each document being processed has an entry in `SessionStore` for the duration of its assessment.

```python
DocumentSession(
    doc_id             = "3fa85f64...",
    template_type      = "SDA",
    s3_key             = "3fa85f64..._architecture-v2.docx",
    expected_task_ids  = {"3fa85f64..._security"},   # one per dispatched agent
    collected_results  = {},                          # filled as results arrive
    started_at         = datetime(...),
    completion_event   = asyncio.Event(),             # set when all expected arrive
)
```

**Lifecycle:**
- Created when `_process_document` dispatches tasks
- Updated by `_status_queue_poller` each time a `StatusMessage` arrives
- `completion_event` is set once `expected_task_ids ⊆ collected_results.keys()`
- Removed after the document reaches a terminal status

**Important:** Session state is in-memory only. A restart of the Orchestrator process loses all in-progress sessions. Those documents remain in `PROCESSING` until the `cleanup_stuck_documents` job (runs every 5 minutes) resets them so they can be re-triggered.

---

## Timeout and Terminal Statuses

Configured via `ORCHESTRATOR_AGENT_TIMEOUT_SECONDS` (default `480` seconds / 8 minutes).

| Condition | Status written | `result_md` | `error_message` |
|-----------|---------------|-------------|-----------------|
| All expected results received before timeout | `COMPLETE` | Full report | `null` |
| Timeout with ≥ 1 result | `PARTIAL_COMPLETE` | Partial report | Lists non-responding agent types |
| Timeout with 0 results | `ERROR` | `null` | `"No agent responses received within timeout."` |
| S3 download or text extraction failure | `ERROR` | `null` | Exception message |

---

## How Final Status Reaches the Frontend

The Orchestrator **never contacts the frontend directly**. The communication path is:

```
Orchestrator
    │
    │  UPDATE document_uploads
    │  SET status = 'COMPLETE' / 'PARTIAL_COMPLETE' / 'ERROR'
    │      result_md = '# Assessment Report...'
    ▼
PostgreSQL (RDS)
    │
    │  SELECT status, result_md FROM document_uploads
    │  WHERE doc_id = $1 AND user_id = $2
    ▼
CoreBackend  ◄──── GET /api/v1/documents/status         ◄──── Frontend (polls every 30s)
             ◄──── GET /api/v1/documents/{documentId}   ◄──── Frontend (on COMPLETE)
```

**Step-by-step:**

1. **Frontend uploads** → CoreBackend returns `{ documentId, status: "PROCESSING" }`
2. **Frontend polls** `GET /api/v1/documents/status` every 30 seconds
3. CoreBackend queries `document_uploads WHERE status = 'PROCESSING'` and returns the list of in-progress IDs
4. **Orchestrator writes** the terminal status and `result_md` to the database when processing completes
5. On the **next poll**, the document ID is absent from the processing list — frontend knows it's done
6. **Frontend fetches** `GET /api/v1/documents/{documentId}` — CoreBackend reads `result_md` and status from the database and returns the full record

The database is the **only shared state** between Orchestrator and CoreBackend. Neither service calls the other after the initial `POST /orchestrate`.

The full frontend polling contract is documented in [corebackend-api.md — Integration Cookbook](./corebackend-api.md#integration-cookbook).

---

## Configuration Reference

| Environment Variable | Config path | Default | Description |
|----------------------|-------------|---------|-------------|
| `ORCHESTRATOR_URL` | `config.orchestrator.url` | `http://localhost:8001` | URL CoreBackend uses to call this service |
| `ORCHESTRATOR_PORT` | `config.orchestrator.port` | `8001` | Port this service listens on |
| `ORCHESTRATOR_AGENT_TIMEOUT_SECONDS` | `config.orchestrator.agent_timeout_seconds` | `480` | Max wait for agent responses |
| `ORCHESTRATOR_DEFAULT_AGENT_TYPE` | `config.orchestrator.default_agent_type` | `general` | Agent type dispatched (single task; fan-out pending) |
| `TASK_QUEUE_URL` | `config.sqs.task_queue_url` | `…/aia-tasks` | SQS queue published to |
| `STATUS_QUEUE_URL` | `config.sqs.status_queue_url` | `…/aia-status` | SQS queue polled for results |

---

## Running the Orchestrator

```bash
# Development (with auto-reload)
uvicorn app.orchestrator.main:app --host 127.0.0.1 --port 8001 --reload

# Production (ECS container entrypoint)
uvicorn app.orchestrator.main:app --host 0.0.0.0 --port 8001
```

Swagger UI (dev only): `http://127.0.0.1:8001/docs`
