# ECS Fargate Architecture — Brainstorm

**Date:** 2026-04-23  
**Participants:** Tabrej Khan, Claude  
**Status:** Decisions made — ready for detailed design

---

## Proposed Architecture

### Overview

Two ECS Fargate PODs:

| POD | Modules | ECS Layout |
|-----|---------|------------|
| Backend POD | CoreBackend, Orchestrator | Two containers, same ECS task |
| Agent POD | All specialist agents (Security, Data, Risk, EA, Solution) | Single container, single ECS service |

### Flow Diagram

```
Frontend
    │  POST /upload
    │  ← 202 { documentId, status: PENDING }
    │
    │  GET /status/{documentId}  (polls every 30 seconds)
    │  ← { status: PROCESSING | COMPLETE | PARTIAL_COMPLETE | ERROR, result? }
    ▼
CoreBackend container (localhost:8000)
    ├── EntraID (Azure AD auth)
    ├── S3 (file upload → s3Key)
    ├── RDS: INSERT document record (status=PENDING)
    ├── returns 202 with documentId to frontend
    └── POST http://localhost:8001/orchestrate  { documentId, s3Key }
             │  ← 202 Accepted immediately (fire-and-forget)
             ▼
    Orchestrator container (localhost:8001)  [same ECS task, async background task]
            ├── RDS: UPDATE status=PROCESSING
            ├── reads file from S3 → extracts text content
            ├── generates 5 task messages (one per agentType, fileContent embedded)
            ├── publishes all 5 to SQS: aia-tasks
            ├── in-memory: { documentId → { pendingTaskIds, collectedResults } }
            └── polls SQS: aia-status until all 5 responses received
                        ▲
                        │ (JSON keyed by taskId)
    SQS: aia-tasks  (1 queue)
            │
            ▼
    Relay Service — ECS Fargate (single container, all agent logic)
            ├── polls aia-tasks
            ├── reads agentType → dispatches to internal handler
            │       security  → SecurityHandler  → Bedrock
            │       data      → DataHandler      → Bedrock
            │       risk      → RiskHandler      → Bedrock
            │       ea        → EaHandler        → Bedrock
            │       solution  → SolutionHandler  → Bedrock
            └── publishes result → SQS: aia-status  { taskId, agentType, result }
```

### Completion Flow

Once Orchestrator has collected all N agent responses:
1. `DeterministicSummary.generate(results)` — formats structured JSON → `.md` sections
2. RDS: UPDATE document record — `status=COMPLETE`, store `.md` content, `completedAt` timestamp
3. Frontend next poll sees `COMPLETE` and fetches result
4. _(Future)_ swap to `BedrockSummary` with no agent code changes

### Document Status Lifecycle

```
UPLOADING → UPLOADED → PENDING → PROCESSING → COMPLETE
                ↘          ↘                ↘
               ERROR      ERROR       PARTIAL_COMPLETE (≥1 agent, threshold reached)
                                            ↘
                                           ERROR (0 agents or hard failure)
```

---

### CoreBackend Services

- `user` — user management
- `health` — health check endpoint
- `upload` — file upload to S3
- `fetchHistory` — retrieve past assessments from RDS
- (others TBD)

**External integrations:** EntraID, S3, RDS PostgreSQL

---

## Decisions

### 1. Task Routing → 1 task queue + 1 Agent service with internal dispatch (no SNS)

**Decision:** Single SQS task queue. All agent logic runs inside one ECS service. Service reads `agentType` and dispatches internally to the right handler.

**Why 1 queue works here:**
- All 5 agents are in one service — one consumer group polls the queue
- No competing-consumer problem (only one service receives each message)
- Internal dispatch by `agentType` is a simple dict/match lookup — no message churn, no re-queuing
- Fewer AWS resources: 1 task queue + 1 DLQ instead of 5 + 5

**Rejected options:**
- *5 separate queues with 5 separate agent services* — unnecessary complexity for POC; independent scaling not yet needed
- *Single queue + `ChangeMessageVisibility(0)` re-queue* — message churn, latency proportional to agent count, DLQ risk
- *SNS fan-out* — correct for multiple independent subscribers; overkill when one service handles all types

**Queue topology (POC):**

| Queue | Purpose | Consumer |
|-------|---------|----------|
| `aia-tasks` | All agent task messages | Relay Service (single ECS service) |
| `aia-tasks-dlq` | Unprocessable task messages | Ops / alerting |
| `aia-status` | Agent result messages | Orchestrator |
| `aia-status-dlq` | Unprocessable status messages | Ops / alerting |

**Relay Service internal dispatch:**
```python
handlers: dict[str, AgentHandler] = {
    "security": SecurityHandler(),
    "data":     DataHandler(),
    "risk":     RiskHandler(),
    "ea":       EaHandler(),
    "solution": SolutionHandler(),
}

task = TaskMessage.model_validate_json(sqs_record["body"])
result = await handlers[task.agent_type].process(task)
```

**Upgrade path to per-agent queues:**  
If a specific agent needs independent scaling in production (e.g. SecurityAgent uses a heavier model and runs slower), extract it into its own ECS service + its own SQS queue at that point. The rule is simple: **one queue per independently-scaling consumer group**. For POC all agents scale together — 1 queue. Split only when scaling pressure makes it necessary.

---

### 2. CoreBackend ↔ Orchestrator → Two containers, same ECS task

**Decision:** Both modules run as separate containers within the same ECS task definition, communicating over `localhost`.

- CoreBackend → `http://localhost:8001/orchestrate`
- No network hop, no service discovery overhead
- Independent Docker images and codebases — logically separate
- Deployed as a unit for POC simplicity

**Upgrade path:** When independent scaling is needed (Orchestrator is CPU-heavy; CoreBackend handles concurrent HTTP), split into separate ECS services. Interface stays the same — only the URL changes from `localhost` to a service discovery endpoint.

---

### 3. Correlation State → In-memory for POC, Redis for production

**Decision:** In-memory dict within the Orchestrator process for POC.

```python
# Orchestrator in-memory state
pending: dict[str, PendingDocument] = {}

@dataclass
class PendingDocument:
    document_id: str
    expected_task_ids: set[str]
    results: dict[str, AgentResult]
```

**Accepted limitation:** State is lost on Orchestrator restart. Acceptable for POC.

**Production upgrade path:** Replace with **Redis/ElastiCache** (not RDS).  
- Redis: TTL keys, atomic INCR for fan-in counter, sub-millisecond reads  
- RDS: wrong tool — connection overhead, query latency, table bloat for transient counters

---

### 4. Summarisation → Deterministic now, Bedrock-ready interface

**Decision:** Implement a `SummaryGenerator` protocol so the implementation is swappable without touching agent code.

```python
class SummaryGenerator(Protocol):
    def generate(self, results: list[AgentResult]) -> str: ...

class DeterministicSummary:      # current implementation
    def generate(self, results: list[AgentResult]) -> str:
        # formats each AgentResult JSON → structured markdown sections
        ...

class BedrockSummary:            # future implementation
    def generate(self, results: list[AgentResult]) -> str:
        # calls Bedrock with results as context, returns LLM-generated markdown
        ...
```

Config-driven selection — flip between implementations without code changes.

---

### 5. Model Layer → Abstracted, Bedrock default for production

**Decision:** Abstract via a `ModelClient` protocol. Bedrock for AWS-native production; Anthropic direct API available as alternative for dev or models not yet on Bedrock.

```python
class ModelClient(Protocol):
    async def invoke(self, prompt: str, system: str) -> str: ...

class BedrockModelClient:      # production (IAM auth, VPC endpoints, no API key rotation)
    ...

class AnthropicModelClient:    # dev / direct API fallback
    ...
```

Config drives which client loads. No agent code changes when switching.

**Bedrock advantages for AWS-native deployment:**
- IAM-based auth (no API key management)
- VPC endpoint support (traffic stays within AWS)
- Usage visible in AWS Cost Explorer alongside other services
- Access to non-Anthropic models (Llama, Mistral, Titan) if needed later

---

## Decision: CoreBackend ↔ Orchestrator Invocation → Fire-and-forget + polling

**Pattern:**
1. Frontend `POST /upload` → CoreBackend stores file in S3, inserts RDS record (`status=PENDING`), returns `202 { documentId }` immediately
2. CoreBackend fires `POST http://localhost:8001/orchestrate { documentId, s3Key }` — does not wait for completion
3. Orchestrator returns `202 Accepted` immediately and processes asynchronously (asyncio background task)
4. Frontend polls `GET /status/{documentId}` on CoreBackend at a sensible interval (e.g. every 5s)
5. CoreBackend reads `status` + `result` from RDS and returns current state
6. Orchestrator writes `COMPLETE` + `.md` result to RDS when all agents respond; next poll returns it

**Why this pattern:**
- Document processing takes 30–120 seconds — synchronous HTTP would time out or block threads
- `documentId` is the shared key — CoreBackend and Orchestrator never need to talk again after the initial fire
- Frontend UX: show progress indicator, reveal result when status flips to `COMPLETE`
- Error handling: any stage failure writes `status=ERROR` + `errorMessage` to RDS; next poll surfaces it

**RDS document record:**

| Column | Type | Notes |
|--------|------|-------|
| `document_id` | UUID PK | shared key across all components |
| `user_id` | UUID FK | owner |
| `s3_key` | TEXT | uploaded file location |
| `status` | ENUM | PENDING, PROCESSING, COMPLETE, ERROR |
| `result_md` | TEXT | populated on COMPLETE |
| `error_message` | TEXT | populated on ERROR |
| `created_at` | TIMESTAMP | |
| `completed_at` | TIMESTAMP | populated on COMPLETE/ERROR |

**Decision:** Single top-level status only. No per-agent granular tracking.  
Frontend shows a single progress state — no "3 of 5 agents complete" breakdown needed.

### Document Status Values

| Status | Set by | Meaning |
|--------|--------|---------|
| `UPLOADING` | CoreBackend (upload start) | File transfer to S3 in progress |
| `UPLOADED` | CoreBackend (upload complete) | File stored in S3; AV scan slot — document waits here until cleared *(scan not yet implemented in POC)* |
| `PENDING` | CoreBackend (after upload / future: after AV pass) | File cleared and queued; Orchestrator not yet started |
| `PROCESSING` | Orchestrator (on pickup) | Text extraction running and/or agent tasks dispatched and awaiting results |
| `COMPLETE` | Orchestrator (all agents responded within threshold) | Full assessment done; `result_md` populated in RDS |
| `PARTIAL_COMPLETE` | Orchestrator (threshold reached, ≥1 agent responded) | Partial assessment; `result_md` contains results from responding agents only; `error_message` lists non-responding agent types |
| `ERROR` | CoreBackend or Orchestrator (unrecoverable failure, or 0 agents responded within threshold) | `error_message` populated; no further processing |

**State machine:**

```
UPLOADING → UPLOADED → PENDING → PROCESSING → COMPLETE
     ↘           ↘                    ↘
    ERROR       ERROR          PARTIAL_COMPLETE (≥1 agent responded, threshold reached)
  (upload     (AV fail,              ↘
   failed)    — future)             ERROR (0 agents responded within threshold)
                                    ERROR (extraction or compile failure)
```

**Transition rules:**
- CoreBackend sets `UPLOADING` the moment upload begins, `UPLOADED` when S3 confirms receipt
- In POC: CoreBackend advances directly `UPLOADED → PENDING` (no AV step)
- In future: AV scanner advances `UPLOADED → PENDING` on pass, `UPLOADED → ERROR` on fail
- Orchestrator sets `PROCESSING` before extraction begins (first RDS write after receiving fire-and-forget call)
- Orchestrator sets `COMPLETE` when all N agent results arrive before threshold
- Orchestrator sets `PARTIAL_COMPLETE` when threshold is reached and at least 1 agent has responded; `result_md` is built from available results; `error_message` lists missing agent types (e.g. `"Agents did not respond within threshold: ea, solution"`)
- Orchestrator sets `ERROR` when threshold is reached and 0 agents responded, or when extraction/compile fails

**Threshold config (Orchestrator):**
```yaml
orchestrator:
  agent_response_timeout_seconds: 480   # 8 minutes — configurable, reviewed after real Bedrock latencies known
```

**RDS ENUM:**
```sql
CREATE TYPE document_status AS ENUM (
    'UPLOADING',
    'UPLOADED',
    'PENDING',
    'PROCESSING',
    'COMPLETE',
    'PARTIAL_COMPLETE',
    'ERROR'
);
```

**Frontend behaviour by status:**

| Status | Terminal | UI state |
|--------|----------|----------|
| `UPLOADING` | No | "Processing..." |
| `UPLOADED` | No | "Processing..." |
| `PENDING` | No | "Processing..." |
| `PROCESSING` | No | "Processing..." |
| `COMPLETE` | **Yes** | Show full assessment result |
| `PARTIAL_COMPLETE` | **Yes** | Show partial result with warning listing non-responding agents |
| `ERROR` | **Yes** | Show error message |

**Frontend rule:** show "Processing..." for all non-terminal statuses. Only update the UI when a terminal status (`COMPLETE`, `PARTIAL_COMPLETE`, `ERROR`) is received. The frontend does not need to distinguish between `UPLOADING`, `PENDING`, and `PROCESSING` — they all look the same to the user.

**Future status additions (not for POC):**

| Status | When needed |
|--------|-------------|
| `CANCELLED` | User aborts an in-progress assessment |
| `REPROCESSING` | Failed/partial document resubmitted without re-uploading |

---

## Message Schemas

### Task Message

Published by Orchestrator **directly** to the target per-agent SQS queue (no SNS).  
Orchestrator resolves `agentType → queue_url` from config at publish time.

**Message body (JSON):**
```json
{
  "taskId":       "doc-uuid_security",
  "documentId":   "doc-uuid",
  "agentType":    "security",
  "templateType": "SDA",
  "fileContent":  "<extracted text content from document>",
  "s3Bucket":     "aia-documents",
  "s3Key":        "uploads/doc-uuid/filename.pdf",
  "userId":       "user-uuid",
  "createdAt":    "2026-04-23T10:00:00Z"
}
```

**Field notes:**
- `taskId` is deterministic: `{documentId}_{agentType}` — Orchestrator builds the full expected set upfront, no separate mapping needed
- `templateType` carried from the document record — agent uses `agentType + templateType` to look up the correct queries JSON from RDS
- `fileContent` is populated by Orchestrator after reading and extracting text from the S3 file — agents do **no document I/O**, only LLM processing
- `fileContent` is `null` when content exceeds the SQS message size limit — agent falls back to fetching via `s3Key`
- `s3Bucket` + `s3Key` serve as the fallback content source and audit reference
- `userId` carried for audit / access control

**Agent content resolution (two-line pattern):**
```python
content = task.file_content or fetch_from_s3(task.s3_bucket, task.s3_key)
```

**SQS message size:** SQS standard limit is 256KB; verify current AWS docs for any increase.  
S3 fallback (`fileContent=null` + `s3Key`) handles content that exceeds the limit regardless of threshold.

**No SNS envelope:** message body is the task JSON directly — no unwrapping needed.

---

### Status Message

Published by Agent to SQS queue `aia-status`.  
Consumed by Orchestrator to collect results and drive fan-in.

**Message body (JSON):**
```json
{
  "taskId":       "doc-uuid_security",
  "documentId":   "doc-uuid",
  "agentType":    "security",
  "status":       "SUCCESS",
  "result": {
    "agentType": "security",
    "summary":   "Summary: 0 Red, 2 Amber, 2 Green",
    "sourceDocument": {
      "filename": "Security-Control-Matrix.docx",
      "url":      "https://sharepoint.com/sites/policies/Security-Control-Matrix.docx"
    },
    "assessments": [
      {
        "questionId": "Q1",
        "rating":     "GREEN",
        "comments":   "The document defines encryption at rest using AES-256.",
        "section":    "Section 3.2, Page 11"
      },
      {
        "questionId": "Q2",
        "rating":     "RED",
        "comments":   "No mention of penetration testing cadence found in the document.",
        "section":    "Section 2.1, Page 6"
      }
    ]
  },
  "errorMessage": null,
  "completedAt":  "2026-04-23T10:01:30Z"
}
```

**Field notes:**
- `status`: `"SUCCESS"` or `"ERROR"` — Orchestrator treats any `ERROR` as a pipeline failure for that document
- `result` is `null` when `status = "ERROR"`; `errorMessage` is `null` when `status = "SUCCESS"`
- `result.summary` is the agent-level count string: e.g. `"Summary: 0 Red, 2 Amber, 2 Green"`
- `result.sourceDocument` carries `filename` and `url` once at the agent result level — sourced from the queries JSON; DeterministicSummary renders these once per section heading in `resultMd`
- `result.assessments[].section` is the policy document section reference sourced from the queries JSON per query
- `result.assessments` is the per-question breakdown consumed by `DeterministicSummary`
- **Size guard:** if `result` JSON exceeds ~200KB, store it in S3 (`results/doc-uuid/security.json`) and replace `result` with `{ "s3Ref": "results/doc-uuid/security.json" }` — Orchestrator detects and fetches before aggregation. For POC, inline is fine.

---

### Orchestrator Fan-in Logic (using deterministic taskId)

```python
# On publishing tasks for a document:
expected = {
    f"{document_id}_security",
    f"{document_id}_data",
    f"{document_id}_risk",
    f"{document_id}_ea",
    f"{document_id}_solution",
}
pending[document_id] = PendingDocument(expected_task_ids=expected, results={})

# On receiving each status message:
pending[document_id].results[task_id] = status_message
if pending[document_id].results.keys() == pending[document_id].expected_task_ids:
    # all agents responded — run DeterministicSummary and write to RDS
```

No UUID lookup table, no counter — set comparison is sufficient.

---

## Queue Topology Detail

### Queue Inventory

| Queue | Direction | DLQ |
|-------|-----------|-----|
| `aia-tasks` | Orchestrator → Relay Service | `aia-tasks-dlq` |
| `aia-status` | Relay Service → Orchestrator | `aia-status-dlq` |

> Queue names subject to change. Values noted here are for POC baseline.

---

### Concept: Long Polling

SQS has two polling modes:

- **Short polling (default):** Consumer calls `ReceiveMessage`, SQS queries only a subset of its servers and returns immediately — even if the queue is empty. The consumer loops constantly, burning API calls and CPU on empty responses.
- **Long polling (`ReceiveMessageWaitTimeSeconds=20`):** SQS holds the connection open for up to 20 seconds waiting for a message. If a message arrives mid-wait it is returned immediately; if nothing arrives in 20s it returns empty and the consumer loops again.

Always use 20s — it is the AWS recommended maximum. Significantly reduces empty API calls, lowers cost, and reduces CPU usage in the consumer.

---

### Concept: DLQ and maxReceiveCount

When a consumer receives a message but fails to process it (exception, crash, timeout), the message is **not deleted**. After the visibility timeout expires, SQS makes it visible again for retry.

`maxReceiveCount=3` means SQS allows the message to be received up to 3 times total. On the 4th receive attempt, SQS automatically moves it to the **Dead Letter Queue (DLQ)** instead of delivering it again.

Without a DLQ, a *poison pill* message — one that always causes a crash — loops forever, consuming retries, blocking throughput, and generating alert noise. The DLQ quarantines it so the main queue stays healthy and ops can inspect the failed message at their own pace.

---

### aia-tasks

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Visibility timeout | **600s (10 min)** | Must exceed worst-case agent processing time. Agent calls Bedrock with potentially large content — 10 min provides a safe buffer. If timeout expires before the agent deletes the message, SQS re-delivers it causing duplicate processing. Revisit once real Bedrock latencies are measured. |
| Message retention | **24 hours** | Tasks should be picked up within seconds. 24h is a generous safety net; if a task sits longer something is broken and ops should be alerted. |
| Receive wait time | **20s (long polling)** | SQS holds the connection open up to 20s waiting for a message. Reduces empty `ReceiveMessage` API calls, lowers cost, reduces consumer CPU usage. |
| Max message size | **256KB** (SQS standard) | `fileContent` embedded in message body; S3 fallback (`fileContent=null`) for oversized content. Verify current AWS limit. |
| DLQ redrive — maxReceiveCount | **3** | Message can be received and failed up to 3 times before being moved to `aia-tasks-dlq`. Allows 2 retries for transient Bedrock errors before declaring permanent failure. |

---

### aia-status

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Visibility timeout | **180s (3 min)** | Orchestrator processing: read message → update in-memory state → write RDS on completion. 3 min is generous for this fast path. |
| Message retention | **24 hours** | Status messages should be consumed within seconds of arrival. Matches task queue retention for consistency. |
| Receive wait time | **20s (long polling)** | Same as aia-tasks — reduces empty polls. |
| DLQ redrive — maxReceiveCount | **3** | If Orchestrator cannot process a status message after 3 attempts, move to `aia-status-dlq` for investigation. |

---

### DLQs (aia-tasks-dlq, aia-status-dlq)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Message retention | **14 days** | Maximum SQS retention. Gives ops maximum time to inspect failed messages and manually reprocess or discard. |
| Alarm | CloudWatch alarm on `ApproximateNumberOfMessagesVisible > 0` | Any message in a DLQ means processing failed 3 times. Page ops immediately for investigation. |

---

### Key Rule: Visibility Timeout > Max Processing Time

If a consumer takes longer than the visibility timeout, the message becomes visible again and SQS re-delivers it — potentially causing duplicate processing. Always set visibility timeout with a buffer above the observed worst-case processing time. Revisit `aia-tasks` timeout once real Bedrock response times are measured in production.

---

## ECS Task Definitions

### Backend POD — Task Definition

Two containers in one ECS task. Communicate over `localhost`. Deployed and scaled as a unit.

**Task-level resource allocation:**

| | Value |
|-|-------|
| CPU | 2048 (2 vCPU) |
| Memory | 4096 MB (4 GB) |
| Network mode | `awsvpc` |
| Task role | `aia-backend-task-role` |

**Container: corebackend**

| Parameter | Value |
|-----------|-------|
| CPU | 512 |
| Memory | 1024 MB |
| Port | 8000 |
| Health check | `GET http://localhost:8000/health` — interval 30s, timeout 5s, retries 3 |
| Image | ECR: `aia-corebackend:latest` |

Environment variables:
```
APP_PORT=8000
ORCHESTRATOR_URL=http://localhost:8001
DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
S3_BUCKET=aia-documents
```

**Container: orchestrator**

| Parameter | Value |
|-----------|-------|
| CPU | 1024 |
| Memory | 2048 MB |
| Port | 8001 |
| Health check | `GET http://localhost:8001/health` — interval 30s, timeout 5s, retries 3 |
| Image | ECR: `aia-orchestrator:latest` |

Environment variables:
```
APP_PORT=8001
DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
S3_BUCKET=aia-documents
SQS_TASKS_QUEUE_URL
SQS_STATUS_QUEUE_URL
AGENT_RESPONSE_TIMEOUT_SECONDS=480
```

**Scaling note:** Multiple Backend POD instances are safe — each ECS task instance has its own Orchestrator container with independent in-memory state. CoreBackend always calls `localhost:8001` so it hits the Orchestrator in the same task, never a different instance. When in-memory state is migrated to Redis in production, scaling becomes even simpler.

---

### Agent POD — Task Definition

Single container. Consumer service — no inbound traffic, only SQS polling and Bedrock calls.

**Task-level resource allocation:**

| | Value |
|-|-------|
| CPU | 1024 (1 vCPU) |
| Memory | 2048 MB (2 GB) |
| Network mode | `awsvpc` |
| Task role | `aia-agent-task-role` |

**Container: relay-service**

| Parameter | Value |
|-----------|-------|
| CPU | 1024 |
| Memory | 2048 MB |
| Port | 8080 (health endpoint only — no public traffic) |
| Health check | `GET http://localhost:8080/health` — interval 30s, timeout 5s, retries 3 |
| Image | ECR: `aia-relay-service:latest` |

Environment variables:
```
SQS_TASKS_QUEUE_URL
SQS_STATUS_QUEUE_URL
BEDROCK_REGION
BEDROCK_SECURITY_MODEL_ID
BEDROCK_DATA_MODEL_ID
BEDROCK_RISK_MODEL_ID
BEDROCK_EA_MODEL_ID
BEDROCK_SOLUTION_MODEL_ID
DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
```

**Health check implementation note:** Relay Service has no HTTP server by nature (it is a queue consumer). Add a minimal health endpoint that verifies the polling loop is running — a lightweight FastAPI or http.server instance on port 8080 returning `{"status": "ok"}`. ECS uses this to detect crashed containers.

---

### IAM Task Roles (summary — detail in Bedrock IAM section)

**aia-backend-task-role** (shared by CoreBackend and Orchestrator containers):

| Service | Actions | Resource |
|---------|---------|----------|
| S3 | `PutObject`, `GetObject` | `aia-documents/*` |
| SQS | `SendMessage` | `aia-tasks` |
| SQS | `ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility` | `aia-status` |
| CloudWatch Logs | `CreateLogGroup`, `CreateLogStream`, `PutLogEvents` | `*` |

**aia-agent-task-role:**

| Service | Actions | Resource |
|---------|---------|----------|
| SQS | `ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility` | `aia-tasks` |
| SQS | `SendMessage` | `aia-status` |
| S3 | `GetObject` | `aia-documents/*` (fallback when `fileContent` is null) |
| Bedrock | `bedrock:InvokeModel` | specific model ARNs |
| CloudWatch Logs | `CreateLogGroup`, `CreateLogStream`, `PutLogEvents` | `*` |

---

### Auto-scaling

**Backend POD:**
- Policy: CPU target tracking at 70%
- Min tasks: 1, Max tasks: 4 (POC)

**Agent POD:**
- Policy: SQS target tracking on `aia-tasks` — `ApproximateNumberOfMessagesVisible`
- Target: 1 task per N messages (tune after load testing — start with 5)
- Min tasks: 1, Max tasks: 10 (POC)
- Scale-in cooldown: 300s (avoid thrashing when queue drains quickly)

---

### Resource Sizing Note

All CPU and memory values are **POC baselines**. Orchestrator text extraction (PDF/DOCX) and Agent Bedrock call latency are the two unknowns that will drive real sizing. Revisit after first load test.

---

## Bedrock IAM Permissions

### Step 1 — Enable Model Access (AWS Console, done once per region)

**This is separate from IAM and must be done before any `InvokeModel` call will succeed.**

1. AWS Console → Amazon Bedrock → **Model access**
2. Request access to the required Anthropic Claude models
3. Accept the model provider terms (Anthropic end-user licence)
4. Access approval: usually minutes, occasionally up to a few hours
5. Must be repeated for **each AWS region** you deploy to

> **Region note:** Verify Claude model availability in `eu-west-2` before deploying. Some newer Claude models are only available in `us-east-1` / `us-west-2` natively. If a model is unavailable in your region, use a **cross-region inference profile** (see below).

---

### Step 2 — IAM Policy for aia-agent-task-role

Attached to the Agent POD ECS task role. Scoped to specific model ARNs — not wildcard.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockInvokeModels",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:eu-west-2::foundation-model/anthropic.claude-sonnet-4-5",
        "arn:aws:bedrock:eu-west-2::foundation-model/anthropic.claude-opus-4-5"
      ]
    }
  ]
}
```

> Confirm exact Bedrock model IDs in AWS Console → Bedrock → Foundation models. Model IDs on Bedrock follow the pattern `anthropic.claude-{variant}` and differ from Anthropic API model names.

**`bedrock:InvokeModelWithResponseStream`** is included for future streaming support at no extra cost — safe to include now.

---

### Step 3 — Trust Policy (ECS tasks assume the role)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

---

### Cross-Region Inference Profiles (if model unavailable in eu-west-2)

If a required Claude model is not available in `eu-west-2`, use an AWS-managed cross-region inference profile. Bedrock routes the request to a region where the model is available, transparently.

**Profile ARN format:**
```
arn:aws:bedrock:{home-region}:{account-id}:inference-profile/{profile-id}
```

**IAM resource change** — replace foundation-model ARNs with inference profile ARNs:
```json
"Resource": [
  "arn:aws:bedrock:eu-west-2:{ACCOUNT_ID}:inference-profile/eu.anthropic.claude-sonnet-4-5-v1:0",
  "arn:aws:bedrock:eu-west-2:{ACCOUNT_ID}:inference-profile/eu.anthropic.claude-opus-4-5-v1:0"
]
```

Also add permission to use the profile:
```json
{
  "Sid": "BedrockInferenceProfiles",
  "Effect": "Allow",
  "Action": "bedrock:GetInferenceProfile",
  "Resource": "*"
}
```

---

### VPC Endpoint (production)

For production, add a Bedrock VPC interface endpoint to keep traffic within the AWS network — no internet gateway required for Bedrock calls.

| Parameter | Value |
|-----------|-------|
| Service name | `com.amazonaws.eu-west-2.bedrock-runtime` |
| Type | Interface endpoint |
| Subnets | Private subnets where Agent POD tasks run |
| Security group | Allow HTTPS (443) from Agent POD security group |

Once the endpoint is in place, Bedrock SDK calls from the Relay Service route through it automatically — no code change needed.

---

### Relay Service config mapping (model IDs from environment)

Each agent type uses its own model — configurable independently:

```
BEDROCK_SECURITY_MODEL_ID=anthropic.claude-opus-4-5     # heavier model for security
BEDROCK_DATA_MODEL_ID=anthropic.claude-sonnet-4-5
BEDROCK_RISK_MODEL_ID=anthropic.claude-sonnet-4-5
BEDROCK_EA_MODEL_ID=anthropic.claude-sonnet-4-5
BEDROCK_SOLUTION_MODEL_ID=anthropic.claude-sonnet-4-5
```

Swapping a model for a specific agent requires only an environment variable change — no code deployment.

---

## Polling Interval Strategy

### Context

Frontend polls `GET /status/{documentId}` on CoreBackend after submitting a document. CoreBackend reads the `status` column from RDS and returns the current state. Polling continues until a terminal status is received or the frontend timeout is reached.

**Frontend rendering rule:** show "Processing..." for all non-terminal statuses. Only update the UI when a terminal status arrives — the frontend does not distinguish between `UPLOADING`, `PENDING`, or `PROCESSING`.

**Terminal statuses** (stop polling, update UI):
- `COMPLETE` → render full result
- `PARTIAL_COMPLETE` → render partial result with warning
- `ERROR` → render error message

**Non-terminal statuses** (keep polling, show "Processing..."):
- `UPLOADING`, `UPLOADED`, `PENDING`, `PROCESSING`

---

### Polling Interval — Fixed

| Parameter | Value |
|-----------|-------|
| Interval | **30 seconds** |
| Max wait | **10 minutes (600s)** |
| Worst-case polls | 600 ÷ 30 = **20 polls maximum per document** |

**Max frontend wait: 10 minutes (600s)**
- Orchestrator agent timeout: 8 minutes
- Buffer for extraction, compile, RDS write: ~2 minutes
- After 10 minutes with no terminal status, frontend stops polling and shows a timeout message

At 30-second polling, a user may wait up to 30 seconds after the assessment completes before the UI updates. Acceptable for a document assessment tool where processing itself takes 30–120 seconds.

---

### Frontend Timeout Behaviour

When the 10-minute frontend timeout is reached without a terminal status:

- Show message: *"This assessment is taking longer than expected. You can leave this page and check back later."*
- **Do not cancel the backend process** — Orchestrator continues running
- Document record in RDS retains its current status
- User can return and call `GET /status/{documentId}` at any time to resume checking

---

### API Response Shape

`resultMd` is never returned by the status endpoint — it is fetched only when the user clicks "View Result" via `GET /documents/{documentId}`.

On any non-terminal status:
```json
{
  "documentId": "doc-uuid",
  "status": "PROCESSING",
  "errorMessage": null,
  "createdAt": "2026-04-23T10:00:00Z",
  "completedAt": null
}
```

On `COMPLETE`:
```json
{
  "documentId": "doc-uuid",
  "status": "COMPLETE",
  "errorMessage": null,
  "createdAt": "2026-04-23T10:00:00Z",
  "completedAt": "2026-04-23T10:01:45Z"
}
```

On `PARTIAL_COMPLETE`:
```json
{
  "documentId": "doc-uuid",
  "status": "PARTIAL_COMPLETE",
  "errorMessage": "Agents did not respond within threshold: ea, solution",
  "createdAt": "2026-04-23T10:00:00Z",
  "completedAt": "2026-04-23T10:08:15Z"
}
```

---

### Pseudo-code (Frontend)

```typescript
const POLL_INTERVAL_MS = 30_000
const MAX_WAIT_MS = 600_000
const TERMINAL = new Set(['COMPLETE', 'PARTIAL_COMPLETE', 'ERROR'])

async function pollStatus(documentId: string) {
  const startTime = Date.now()

  showProcessing()  // shown immediately, held until a terminal status arrives

  while (Date.now() - startTime < MAX_WAIT_MS) {
    const response = await getStatus(documentId)

    if (TERMINAL.has(response.status)) {
      renderResult(response)  // replaces "Processing..." with result, partial result, or error
      return
    }

    // non-terminal (UPLOADING / UPLOADED / PENDING / PROCESSING) — stay on "Processing..."
    await sleep(POLL_INTERVAL_MS)
  }

  renderTimeoutMessage()  // backend continues running; user can return and check later
}
```

---

### Upgrade Path — Server-Sent Events (SSE)

When concurrent user load grows, polling can be replaced with SSE. The server holds one HTTP connection open per user and pushes a single event the moment a terminal status is written — eliminating all unnecessary RDS reads.

**How it would work:** CoreBackend exposes `GET /status/{documentId}/stream`. Orchestrator (same ECS task, localhost) calls an internal notify endpoint on CoreBackend when done. CoreBackend pushes the event to the waiting connection. Frontend uses the native browser `EventSource` API — no library needed. Only CoreBackend and frontend change; Orchestrator, RDS, and agents are untouched.

**Pros:**
- Instant UI update — no up-to-30s lag waiting for the next poll
- Dramatically fewer RDS reads under concurrent load (1 read per completion vs N polls per user)
- Simple browser API with built-in auto-reconnect

**Cons:**
- AWS ALB drops idle connections after 60s — CoreBackend must send a `:heartbeat` comment every ~30s to keep the connection alive
- Stateful connections — if CoreBackend and Orchestrator are ever split into separate services, a Redis pub/sub layer is needed to fan notifications across CoreBackend instances
- Missed events on reconnect — if the connection drops exactly when the terminal event is sent, the frontend must do a single poll on reconnect to catch up
- Corporate proxies may buffer the stream — requires `Cache-Control: no-cache` and `X-Accel-Buffering: no` response headers

---

## CoreBackend Services

### Common Conventions

**Base URL:** `https://{alb-dns}/api/v1`

**Error envelope (all error responses):**
```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description"
}
```

**HTTP status codes used:**

| Code | When |
|------|------|
| 200 | Successful GET |
| 202 | Upload accepted (async processing started) |
| 400 | Validation failure (wrong file type, missing field) |
| 401 | Unauthorised (future — SSO token invalid or missing) |
| 404 | documentId not found or not owned by user |
| 413 | File exceeds size limit |
| 500 | Unexpected server error |

---

### Auth Strategy — Guest User (POC) → SSO (future)

**What is AuthMiddleware?**
AuthMiddleware is a request interceptor that runs before every protected endpoint handler. It sits between the incoming HTTP request and the handler function. Its sole job is to resolve *who is making the request* and inject a `UserIdentity` object into the request context. The handler then reads that identity without knowing or caring how it was produced. This pattern is standard in FastAPI via dependency injection — the middleware is declared as a dependency on each route, and FastAPI calls it automatically before the handler runs.

**Design principle:** identity resolution is the only thing that changes when SSO is introduced. All handlers, RDS writes, Orchestrator calls, and response shapes use `userId` as an opaque value — they are completely unaware of how that identity was resolved.

The seam is a single `AuthMiddleware` with two implementations:

```
POC:  AuthMiddleware (GuestMode)
          └── no token required
          └── reads the seeded guestUser record from RDS once at startup
          └── injects fixed UserIdentity { userId, email, name } into every request

Future: AuthMiddleware (SSOMode)
          └── reads Bearer token from Authorization header
          └── validates JWT signature against EntraID JWKS endpoint
          └── checks token expiry, audience, and issuer claims
          └── extracts userId from token claims (oid / sub)
          └── upserts user record in RDS on first login (name + email from claims)
          └── injects resolved UserIdentity { userId, email, name } into the request
```

**Switch is config-driven** — one environment variable (`AUTH_MODE=guest|sso`) determines which implementation loads. No handler code changes.

**Guest user:**
- A single `guestUser` record is seeded in RDS at deployment time with a fixed `userId`, `email`, and `name`
- All POC requests resolve to this user
- All documents created in POC are owned by the guest user's `userId`

**What does NOT change when SSO is integrated:**
- RDS schema — `userId` column stays as-is; real user IDs are just different UUID values
- Request/response shapes — no endpoint adds or removes fields
- Orchestrator, agents, queues — none receive or use user identity
- Document ownership checks — `WHERE document_id = ? AND user_id = ?` works identically for guest or real users

**What DOES change when SSO is integrated:**
- `AuthMiddleware` implementation swapped to SSOMode
- `/users/me` returns token claims instead of seeded guest data
- A user registration/upsert on first login (handled inside the middleware)
- `401 Unauthorised` becomes a possible response on all protected endpoints

---

### 1. Health

```
GET /health
```

No auth required. Used by ECS health check and ALB target group.

**Response 200:**
```json
{ "status": "ok" }
```

---

### 2. Upload

```
POST /documents/upload
Content-Type: multipart/form-data
Authorization: resolved by AuthMiddleware
```

**Request fields:**

| Field | Type | Notes |
|-------|------|-------|
| `file` | File | PDF or DOCX only |
| `templateType` | string | Assessment template to apply (e.g. `SDA`). Stored as VARCHAR — new values can be added without schema changes. |

**Internal flow:**
1. Validate file type (PDF / DOCX) and size (configurable limit)
2. Generate `documentId` (UUID v4)
3. INSERT RDS record: `{ documentId, userId, originalFilename, templateType, status=UPLOADING, createdAt }`
4. Upload file to S3: `uploads/{documentId}/{originalFilename}`
5. UPDATE RDS: `status=UPLOADED`
6. UPDATE RDS: `status=PENDING` *(POC — no AV scan; in future AV scanner drives this transition)*
7. Fire `POST http://localhost:8001/orchestrate { documentId, s3Key }` — no await
8. Return 202

**Response 202:**
```json
{
  "documentId": "doc-uuid",
  "status": "PENDING"
}
```

**Error responses:**

| Scenario | Code | `error` |
|----------|------|---------|
| File type not PDF/DOCX | 400 | `INVALID_FILE_TYPE` |
| File missing from request | 400 | `MISSING_FILE` |
| File exceeds size limit | 413 | `FILE_TOO_LARGE` |
| S3 upload fails | 500 | `UPLOAD_FAILED` |

---

### 3. Status

```
GET /documents/{documentId}/status
Authorization: resolved by AuthMiddleware
```

Returns current status of a document. Called by frontend every 30 seconds until terminal status received. **Does not return `resultMd`** — the full result is fetched separately via `GET /documents/{documentId}` when the user clicks "View Result".

**Frontend UX flow:**
```
Polling → non-terminal status  → show "Processing..."
       → COMPLETE              → show "View Result" button
       → PARTIAL_COMPLETE      → show "View Result" button + warning (which agents missed)
       → ERROR                 → show error message
                                        │
                               user clicks "View Result"
                                        │
                                        ▼
                          GET /documents/{documentId}
                          → returns full record with resultMd
```

**Response 200 — non-terminal:**
```json
{
  "documentId": "doc-uuid",
  "status": "PROCESSING",
  "errorMessage": null,
  "createdAt": "2026-04-23T10:00:00Z",
  "completedAt": null
}
```

**Response 200 — COMPLETE:**
```json
{
  "documentId": "doc-uuid",
  "status": "COMPLETE",
  "errorMessage": null,
  "createdAt": "2026-04-23T10:00:00Z",
  "completedAt": "2026-04-23T10:01:45Z"
}
```

**Response 200 — PARTIAL_COMPLETE:**
```json
{
  "documentId": "doc-uuid",
  "status": "PARTIAL_COMPLETE",
  "errorMessage": "Agents did not respond within threshold: ea, solution",
  "createdAt": "2026-04-23T10:00:00Z",
  "completedAt": "2026-04-23T10:08:15Z"
}
```

**Error responses:**

| Scenario | Code | `error` |
|----------|------|---------|
| documentId not found or not owned by user | 404 | `DOCUMENT_NOT_FOUND` |

---

### 4. History (list)

```
GET /documents
Authorization: resolved by AuthMiddleware
```

Returns paginated list of documents submitted by the user. **Does not include `resultMd`** — use the detail endpoint for full results.

**Query parameters:**

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `page` | int | 1 | Page number (1-based) |
| `limit` | int | 20 | Max 100 |

**Response 200:**
```json
{
  "documents": [
    {
      "documentId": "doc-uuid",
      "originalFilename": "architecture-v2.pdf",
      "templateType": "SDA",
      "status": "COMPLETE",
      "createdAt": "2026-04-23T10:00:00Z",
      "completedAt": "2026-04-23T10:01:45Z"
    }
  ],
  "total": 42,
  "page": 1,
  "limit": 20
}
```

---

### 5. History (detail)

```
GET /documents/{documentId}
Authorization: resolved by AuthMiddleware
```

Returns full document record including `resultMd`. Used when user navigates to a past assessment result.

**Response 200:**
```json
{
  "documentId": "doc-uuid",
  "originalFilename": "architecture-v2.pdf",
  "templateType": "SDA",
  "status": "COMPLETE",
  "resultMd": "# Assessment Report\n\n## Security\n...",
  "errorMessage": null,
  "createdAt": "2026-04-23T10:00:00Z",
  "completedAt": "2026-04-23T10:01:45Z"
}
```

**Error responses:**

| Scenario | Code | `error` |
|----------|------|---------|
| documentId not found or not owned by user | 404 | `DOCUMENT_NOT_FOUND` |

---

### 6. User

```
GET /users/me
Authorization: resolved by AuthMiddleware
```

Returns basic user profile. Minimal for POC — expands when EntraID is integrated.

**Response 200:**
```json
{
  "userId": "user-uuid",
  "email": "user@example.com",
  "name": "Jane Smith"
}
```

> **POC note:** AuthMiddleware resolves to the seeded `guestUser` record. Returns fixed name, email, and userId from RDS. When SSO is integrated, the middleware resolves identity from the JWT token claims instead — this endpoint requires no change.

---

### Endpoint Summary

| Method | Path | Service | Auth |
|--------|------|---------|------|
| GET | `/health` | Health | None |
| POST | `/documents/upload` | Upload | AuthMiddleware |
| GET | `/documents/{documentId}/status` | Status | AuthMiddleware |
| GET | `/documents` | History (list) | AuthMiddleware |
| GET | `/documents/{documentId}` | History (detail) | AuthMiddleware |
| GET | `/users/me` | User | AuthMiddleware |

---

## RDS Schema Design

### Tables Overview

| Table | Purpose |
|-------|---------|
| `users` | User records — guestUser (POC), real users (SSO) |
| `documents` | Main pipeline table — one row per uploaded document |
| `agent_tasks` | Task-level agent responses — one row per agent per document |

---

### users

```sql
CREATE TABLE users (
    user_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Seed data — guestUser (POC):**
```sql
INSERT INTO users (user_id, email, name)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'guest@aia.local',
    'Guest User'
);
```

When SSO is integrated, real users are upserted into this table on first login using claims from the EntraID token. No schema change required.

---

### documents

```sql
CREATE TABLE documents (
    document_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID          NOT NULL REFERENCES users(user_id),
    original_filename VARCHAR(255)  NOT NULL,
    template_type     VARCHAR(50)   NOT NULL,
    s3_bucket         VARCHAR(255)  NOT NULL,
    s3_key            VARCHAR(500)  NOT NULL,
    status            document_status NOT NULL DEFAULT 'UPLOADING',
    result_md         TEXT,
    error_message     TEXT,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    completed_at      TIMESTAMPTZ
);

CREATE INDEX idx_documents_user_id    ON documents(user_id);
CREATE INDEX idx_documents_status     ON documents(status);
CREATE INDEX idx_documents_created_at ON documents(created_at DESC);
```

**Column notes:**

| Column | Notes |
|--------|-------|
| `template_type` | VARCHAR — new template types added without schema migration |
| `status` | ENUM `document_status` — 7 controlled values (UPLOADING → ERROR) |
| `result_md` | Written once by Orchestrator on COMPLETE or PARTIAL_COMPLETE. On all subsequent reads (`GET /documents/{documentId}`), CoreBackend returns this stored value directly from RDS — no reprocessing, no agent calls, no S3 reads. |
| `error_message` | Populated on ERROR; on PARTIAL_COMPLETE lists non-responding agents |
| `completed_at` | Set on COMPLETE, PARTIAL_COMPLETE, and ERROR |
| `s3_bucket` + `s3_key` | Retained for audit, re-processing, and agent S3 fallback |

---

### agent_tasks

Stores task-level detail for each agent invocation. One row per agent per document.

```sql
CREATE TABLE agent_tasks (
    task_id       VARCHAR(255) PRIMARY KEY,
    document_id   UUID         NOT NULL REFERENCES documents(document_id),
    agent_type    VARCHAR(50)  NOT NULL,
    status        VARCHAR(50)  NOT NULL,
    result_json   JSONB,
    error_message TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ
);

CREATE INDEX idx_agent_tasks_document_id ON agent_tasks(document_id);
```

**Column notes:**

| Column | Notes |
|--------|-------|
| `task_id` | Deterministic: `{document_id}_{agent_type}` — matches the task message `taskId` |
| `agent_type` | `security`, `data`, `risk`, `ea`, `solution` |
| `status` | `SUCCESS` or `ERROR` — reflects what the agent returned on `aia-status` queue |
| `result_json` | JSONB — full agent response payload; queryable structure for future reporting |
| `error_message` | Populated when `status=ERROR` |

**Example `result_json`:**
```json
{
  "agentType": "security",
  "summary": "Summary: 1 Red, 1 Amber, 1 Green",
  "sourceDocument": {
    "filename": "Security-Control-Matrix.docx",
    "url": "https://sharepoint.com/sites/policies/Security-Control-Matrix.docx"
  },
  "assessments": [
    {
      "questionId": "Q1",
      "question": "Are encryption standards defined for data at rest and in transit?",
      "rating": "GREEN",
      "comments": "TLS 1.2+ and AES-256 standards are explicitly mandated.",
      "section": "Section 3.2, Page 11"
    },
    {
      "questionId": "Q2",
      "question": "Is there a defined penetration testing cadence?",
      "rating": "RED",
      "comments": "No mention of penetration testing found in the document.",
      "section": "Section 2.1, Page 6"
    }
  ]
}
```

---

### Relationships

```
users (1)
  └── documents (N)
          └── agent_tasks (N)   ← up to 5 per document (one per agent type)
```

---

### Full DDL (in order)

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TYPE document_status AS ENUM (
    'UPLOADING',
    'UPLOADED',
    'PENDING',
    'PROCESSING',
    'COMPLETE',
    'PARTIAL_COMPLETE',
    'ERROR'
);

CREATE TABLE users (
    user_id     UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE documents (
    document_id       UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID            NOT NULL REFERENCES users(user_id),
    original_filename VARCHAR(255)    NOT NULL,
    template_type     VARCHAR(50)     NOT NULL,
    s3_bucket         VARCHAR(255)    NOT NULL,
    s3_key            VARCHAR(500)    NOT NULL,
    status            document_status NOT NULL DEFAULT 'UPLOADING',
    result_md         TEXT,
    error_message     TEXT,
    created_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    completed_at      TIMESTAMPTZ
);

CREATE INDEX idx_documents_user_id    ON documents(user_id);
CREATE INDEX idx_documents_status     ON documents(status);
CREATE INDEX idx_documents_created_at ON documents(created_at DESC);

CREATE TABLE agent_tasks (
    task_id       VARCHAR(255) PRIMARY KEY,
    document_id   UUID         NOT NULL REFERENCES documents(document_id),
    agent_type    VARCHAR(50)  NOT NULL,
    status        VARCHAR(50)  NOT NULL,
    result_json   JSONB,
    error_message TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ
);

CREATE INDEX idx_agent_tasks_document_id ON agent_tasks(document_id);

-- Seed guest user
INSERT INTO users (user_id, email, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'guest@aia.local', 'Guest User');
```

---

## Orchestrator Generating resultMd + Summary Section

### Agent result_json — Updated Schema

`overallRating` replaced with `summary` string. `evidence` + `recommendation` merged into `comments`. `sourceDocument` (filename + URL) moved to agent result level — appears once, not per assessment. Each assessment carries only `section`.

```json
{
  "agentType": "security",
  "summary": "Summary: 0 Red, 2 Amber, 2 Green",
  "sourceDocument": {
    "filename": "Security-Control-Matrix.docx",
    "url": "https://sharepoint.com/sites/policies/Security-Control-Matrix.docx"
  },
  "assessments": [
    {
      "questionId": "Q1",
      "question": "Are authentication and authorization controls defined for all user roles?",
      "rating": "GREEN",
      "comments": "RBAC, MFA for privileged users, and admin separation are documented.",
      "section": "Section 2.1, Page 6"
    },
    {
      "questionId": "Q2",
      "question": "Is incident response end-to-end and time-bound?",
      "rating": "AMBER",
      "comments": "Major steps are documented, but response SLA mapping needs stronger evidence.",
      "section": "Slide 14"
    }
  ]
}
```

> `sourceDocument.url` and `sourceDocument.filename` — originate from the **queries JSON** pre-generated by the Data Pipeline. The Data Pipeline fetches policy documents from SharePoint, uses Bedrock to generate structured queries with section-level references, and stores the queries JSON in RDS. Agents fetch the queries JSON using `agentType + templateType`, pass both the uploaded document content and queries to Bedrock, and echo back `sourceDocument` and `section` from the queries JSON. Reference fields are therefore sourced from the queries JSON, not extracted from the uploaded document. Full details in the **Data Pipeline** section.

---

### agentType → Section Title Mapping

The Orchestrator maps each `agentType` to a human-readable section heading in the markdown:

| agentType | Section heading |
|-----------|----------------|
| `security` | Security Policy |
| `data` | Data Policy |
| `risk` | Risk & Compliance Policy |
| `ea` | Enterprise Architecture |
| `solution` | Solution Designs |

Mapping lives in config — not hardcoded in generation logic.

---

### resultMd Structure

```markdown
# {documentTitle} Evaluation

## {Section 1 — from agentType mapping}
**Reference:** [{filename}]({url})

| Question / Query | Rating | Comments | Section |
|------------------|--------|----------|---------|
| {question} | 🟢 Green | {comments} | {section} |
| {question} | 🟡 Amber | {comments} | {section} |
| {question} | 🔴 Red   | {comments} | {section} |

**{summary}**   ← e.g. "Summary: 0 Red, 2 Amber, 2 Green"

---

## {Section 2}
...

---

## Full Evaluation Summary

{narrative — overall assessment across all responding agents}

### Cross-Category Scorecard
| Category | Red | Amber | Green | Overall |
|----------|-----|-------|-------|---------|
| Security Policy       | 0 | 2 | 2 | Amber-Green |
| Data Policy           | 1 | 2 | 1 | Amber       |
| Risk & Compliance     | 1 | 2 | 1 | Amber       |
| Enterprise Architecture | 0 | 2 | 2 | Amber-Green |
| Solution Designs      | 0 | 2 | 2 | Amber-Green |

### Priority Actions
{all RED-rated items across all agents, listed as numbered actions}
1. Complete DSAR rehearsal and produce timed evidence. *(Data Policy)*
2. Fund and schedule all high-severity risk treatment actions. *(Risk & Compliance)*

### Overall Conclusion
{GREEN / AMBER / RED} — {one-line conclusion}
```

**Rules:**
- Sections appear only for agents that responded — 2 agents = 2 sections, 5 agents = 5 sections
- Section order follows the `agentType` mapping table above (consistent regardless of response order)
- For `PARTIAL_COMPLETE`: a warning banner appears at the top — *"⚠ Partial Assessment: the following domains did not complete within the processing threshold: {missing agent types}."*

---

### Overall Document Rating (deterministic roll-up)

| Condition | Document rating |
|-----------|----------------|
| Any agent has at least one RED | 🔴 RED |
| No RED, at least one AMBER | 🟡 AMBER |
| All GREEN across all responding agents | 🟢 GREEN |

Calculated from responding agents only — missing agents (PARTIAL_COMPLETE) do not contribute.

---

### DeterministicSummary Generation Steps

```
1. Sort responding agent results by agentType mapping order
2. For each agent result:
   a. Render section heading
   b. Render assessment table (one row per question)
   c. Append summary line
3. Parse all summary strings to build cross-category scorecard
4. Collect all RED-rated assessments → Priority Actions list
5. Calculate overall document rating (worst across all agents)
6. Render Full Evaluation Summary section
7. If PARTIAL_COMPLETE: prepend warning banner listing missing agents
8. Return complete markdown string → stored in documents.result_md
```

---

## Audit Logs

### Storage — Two Layers

| Layer | What | Tool |
|-------|------|------|
| Application audit trail | Structured business events (who did what, when, on which document) | RDS `audit_logs` table |
| Infrastructure logs | Container stdout, ALB access logs, ECS task events | CloudWatch Logs |

Application audit events are stored in RDS for easy querying alongside operational data. CloudWatch handles infrastructure and is a separate concern.

---

### RDS audit_logs Table

```sql
CREATE TABLE audit_logs (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type   VARCHAR(100) NOT NULL,
    actor        VARCHAR(100) NOT NULL,
    user_id      UUID         REFERENCES users(user_id),
    document_id  UUID         REFERENCES documents(document_id),
    details      JSONB,
    ip_address   VARCHAR(45),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_user_id     ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_document_id ON audit_logs(document_id);
CREATE INDEX idx_audit_logs_event_type  ON audit_logs(event_type);
CREATE INDEX idx_audit_logs_created_at  ON audit_logs(created_at DESC);
```

**Column notes:**

| Column | Notes |
|--------|-------|
| `event_type` | Uppercase snake case — see event catalogue below |
| `actor` | Who triggered the event: `user`, `orchestrator`, `agent:security`, `agent:data`, etc. |
| `user_id` | The user context — guestUser for all POC events; real user once SSO integrated |
| `document_id` | NULL for events not tied to a specific document (e.g. history list accessed) |
| `details` | JSONB — event-specific context (filename, file size, agent type, error message, etc.) |
| `ip_address` | Captured from HTTP request for user-initiated events; NULL for system/agent events |

---

### Event Catalogue

**CoreBackend — user-initiated events:**

| event_type | actor | Logged when |
|------------|-------|-------------|
| `DOCUMENT_UPLOAD_STARTED` | `user` | CoreBackend receives upload request |
| `DOCUMENT_UPLOADED` | `user` | File successfully stored in S3 |
| `DOCUMENT_UPLOAD_FAILED` | `user` | S3 upload or RDS insert fails |
| `DOCUMENT_RESULT_VIEWED` | `user` | `GET /documents/{documentId}` called |
| `DOCUMENT_HISTORY_ACCESSED` | `user` | `GET /documents` called |

**Orchestrator — pipeline events:**

| event_type | actor | Logged when |
|------------|-------|-------------|
| `DOCUMENT_PROCESSING_STARTED` | `orchestrator` | Orchestrator updates status to PROCESSING |
| `AGENT_TASKS_DISPATCHED` | `orchestrator` | All N tasks published to SQS |
| `RESULT_MD_GENERATED` | `orchestrator` | DeterministicSummary completes |
| `DOCUMENT_PROCESSING_COMPLETED` | `orchestrator` | Status set to COMPLETE |
| `DOCUMENT_PROCESSING_PARTIAL` | `orchestrator` | Status set to PARTIAL_COMPLETE (details: missing agents) |
| `DOCUMENT_PROCESSING_FAILED` | `orchestrator` | Status set to ERROR (details: error message) |

**Relay Service — task-level events:**

| event_type | actor | Logged when |
|------------|-------|-------------|
| `AGENT_TASK_STARTED` | `agent:{agentType}` | Agent picks up task from SQS |
| `AGENT_TASK_COMPLETED` | `agent:{agentType}` | Agent publishes result to aia-status |
| `AGENT_TASK_FAILED` | `agent:{agentType}` | Agent publishes ERROR status to aia-status |

**Frontend — user-initiated events (captured by CoreBackend on API call):**

| event_type | actor | Logged when |
|------------|-------|-------------|
| `UPLOAD_PAGE_ACCESSED` | `user` | User opens the upload form |
| `RESULT_VIEWED` | `user` | User clicks "View Result" button → `GET /documents/{documentId}` |
| `HISTORY_PAGE_ACCESSED` | `user` | User navigates to history list → `GET /documents` |

> **Note on status polling:** `GET /documents/{documentId}/status` fires every 30 seconds — logging every poll would generate significant noise. Status transitions are already captured by Orchestrator events. No audit event for individual poll calls.

**Data Pipeline — pipeline-level events:**

| event_type | actor | Logged when |
|------------|-------|-------------|
| `SHAREPOINT_FETCH_STARTED` | `data_pipeline` | Pipeline begins fetching policy documents from SharePoint |
| `SHAREPOINT_FETCH_COMPLETED` | `data_pipeline` | Documents fetched successfully (details: document count, source URL) |
| `SHAREPOINT_FETCH_FAILED` | `data_pipeline` | SharePoint fetch fails (details: error message) |
| `QUERIES_GENERATION_STARTED` | `data_pipeline` | Bedrock query generation begins (details: document name, templateType) |
| `QUERIES_GENERATION_COMPLETED` | `data_pipeline` | Queries JSON generated successfully (details: query count) |
| `QUERIES_GENERATION_FAILED` | `data_pipeline` | Bedrock generation fails (details: error message) |
| `QUERIES_STORED` | `data_pipeline` | Queries JSON saved to RDS/S3 (details: storage location, templateType) |

---

### Example Log Entries

```json
{
  "event_type": "DOCUMENT_UPLOAD_STARTED",
  "actor": "user",
  "user_id": "user-uuid",
  "document_id": "doc-uuid",
  "details": { "filename": "architecture-v2.pdf", "templateType": "SDA", "fileSizeBytes": 204800 },
  "ip_address": "192.168.1.1"
}

{
  "event_type": "AGENT_TASK_COMPLETED",
  "actor": "agent:security",
  "user_id": "user-uuid",
  "document_id": "doc-uuid",
  "details": { "taskId": "doc-uuid_security", "summary": "Summary: 0 Red, 2 Amber, 2 Green", "durationMs": 14320 },
  "ip_address": null
}

{
  "event_type": "DOCUMENT_PROCESSING_PARTIAL",
  "actor": "orchestrator",
  "user_id": "user-uuid",
  "document_id": "doc-uuid",
  "details": { "respondedAgents": ["security", "data", "risk"], "missingAgents": ["ea", "solution"] },
  "ip_address": null
}
```

---

### Retention Policy

| Storage | Retention | Action after |
|---------|-----------|--------------|
| RDS `audit_logs` | 90 days | Archive to S3 (compressed JSON) |
| S3 archive | 7 years | Expire (S3 lifecycle policy) |
| CloudWatch Logs | 90 days | Expire (CloudWatch log group retention setting) |

---

### Adding audit_logs to Full DDL

Add to RDS schema (after `agent_tasks`):

```sql
CREATE TABLE audit_logs (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type   VARCHAR(100) NOT NULL,
    actor        VARCHAR(100) NOT NULL,
    user_id      UUID         REFERENCES users(user_id),
    document_id  UUID         REFERENCES documents(document_id),
    details      JSONB,
    ip_address   VARCHAR(45),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_user_id     ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_document_id ON audit_logs(document_id);
CREATE INDEX idx_audit_logs_event_type  ON audit_logs(event_type);
CREATE INDEX idx_audit_logs_created_at  ON audit_logs(created_at DESC);
```

---

## Remaining Design Work

1. **ECS task definitions** — container CPU/memory split, health checks, environment variables ✓
2. **Bedrock IAM permissions** — `bedrock:InvokeModel` policy, model access enablement per region ✓
3. **EntraID integration** — ~~MSAL library, token validation middleware in CoreBackend~~ **OUT OF SCOPE** (future)
4. **Polling interval strategy** — frontend polling frequency, max wait, timeout handling ✓
5. **CoreBackend services** — detailed API design for each service endpoint ✓
6. **RDS schema design** — full table definitions including task-level agent response storage ✓
7. **Orchestrator generating resultMd + Summary section** ✓
8. **Audit logs** ✓
9. **Data Pipeline** — Lambda, EventBridge schedule, SharePoint fetch, Bedrock query generation, human review, RDS/S3 storage ✓
10. **Service in Agent consuming Tasks queue** — asyncio poll loop, semaphore concurrency, worker.py owns SQS I/O, handlers are pure ✓
11. **Agent dynamically deciding** — Orchestrator pre-filters active agents (Option A), lazy handler instantiation, 6-hour cache, RDS lookup on miss ✓
12. **resultMd generation logic** — DeterministicSummary algorithm: canonical ordering, section blocks, scorecard, Priority Actions, Overall Conclusion, PARTIAL_COMPLETE banner ✓
13. **GitHub Actions for CI/CD** — deployment pipeline for ECS services (Backend POD + Agent POD) and Data Pipeline Lambda
14. **Frontend** — Hapi/Nunjucks/GOV.UK stack, upload flow, templateType cache, polling (Option A/B TBC), API alignment, history status mapping ✓

---

## Data Pipeline

### Purpose

The Data Pipeline is a Lambda function responsible for:
1. Fetching policy/reference documents from SharePoint
2. Using Bedrock to generate structured **queries JSON** per agent per `templateType`
3. Storing the queries JSON in RDS (POC) for agents to consume
4. Maintaining a catalogue of SharePoint sources and query set versions in RDS

Agents are completely decoupled from the Data Pipeline at runtime — they fetch their own queries JSON directly from RDS and cache it in memory. Orchestrator has **zero visibility** about queries JSON.

---

### Compute — Lambda (not ECS)

Data Pipeline runs as a Lambda function, not an ECS service.

| Concern | Why Lambda fits |
|---------|----------------|
| Execution model | Short-lived, event-driven — runs to completion and exits |
| Frequency | Once daily (1am) + on-demand trigger |
| No persistent state | Each run fetches sources from RDS, processes, stores results, exits |
| Simpler ops | No ECS task definition, no auto-scaling, no container health checks |

---

### Trigger

| Type | Mechanism | When |
|------|-----------|------|
| Scheduled | EventBridge rule (cron: `0 1 * * ? *`) | Daily at 1am — picks up any SharePoint document updates overnight |
| On-demand | EventBridge rule with `source: "aia.admin"` event pattern | Triggered manually or via admin action for immediate refresh |

---

### Data Pipeline Flow

```
EventBridge (1am scheduled / on-demand event)
    │
    ▼
Lambda: Data Pipeline
    ├── fetch document_sources WHERE status='ACTIVE' from RDS
    ├── for each source:
    │   ├── fetch latest SUCCESS row from source_fetch_log for this source_id
    │   ├── check current document last_modified from source (SharePoint, Confluence, web)
    │   ├── if no change (last_modified <= last fetched document_modified_at):
    │   │       INSERT source_fetch_log (status='SKIPPED', skip_reason='No change since last fetch')
    │   │       continue to next source
    │   ├── authenticate via source adapter (MSAL for SharePoint, OAuth for Confluence, etc.)
    │   ├── fetch policy document content
    │   ├── pass document content + agent/templateType context to Bedrock
    │   ├── Bedrock generates queries JSON
    │   │     (structured: filename, url, list of questions with section references)
    │   ├── INSERT into query_sets (status='PENDING_REVIEW')
    │   ├── INSERT source_fetch_log (status='SUCCESS', document_modified_at, query_set_id)
    │   └── write audit event QUERIES_GENERATION_COMPLETED to audit_logs
    └── (future: SNS notification to admin for review — manual in POC)
```

---

### Query JSON Structure (per agent per templateType)

The queries JSON is the contract between the Data Pipeline and the Relay Service. It is generated once by the Data Pipeline and consumed many times by agents.

```json
{
  "templateType": "SDA",
  "version":      "2026-04-24",
  "agent":        "security",
  "filename":     "Security-Control-Matrix.docx",
  "url":          "https://sharepoint.com/sites/policies/Security-Control-Matrix.docx",
  "queries": [
    {
      "queryId":  "Q1",
      "question": "Are authentication and authorization controls defined for all user roles?",
      "reference": {
        "section": "Section 2.1, Page 6"
      }
    },
    {
      "queryId":  "Q2",
      "question": "Is incident response end-to-end and time-bound?",
      "reference": {
        "section": "Slide 14"
      }
    }
  ]
}
```

**Key design points:**
- `filename` and `url` appear **once** at document level — not repeated per query
- Each query carries only a `section` reference — the section/page in the policy document where the answer should be found
- `version` is the date the queries JSON was generated — used for traceability
- `agent` + `templateType` form the lookup key agents use to fetch the right query set

---

### Source Integration

**Source adapter pattern:** the Lambda resolves the correct adapter from `source_type` — each adapter knows how to authenticate, fetch document content, and return `last_modified` date. The Lambda core loop is source-agnostic.

| `source_type` | Auth | Status |
|--------------|------|--------|
| `sharepoint` | MSAL client_credentials | POC |
| `confluence` | OAuth / API token | Future |
| `web` | None / Bearer token | Future |

**SharePoint auth (POC):** MSAL client_credentials flow using env vars already defined in `.env.example`:
```
SHAREPOINT_TENANT_ID
SHAREPOINT_CLIENT_ID
SHAREPOINT_CLIENT_SECRET
```

**Source management:** source URLs are stored in the `document_sources` RDS table — not hardcoded. The Lambda iterates over active sources at runtime. Adding a new source is a data change (INSERT row), not a code deployment.

---

### Human Review Step

New query sets generated by the Data Pipeline are not immediately available to agents. They require admin activation:

```
1. Data Pipeline generates queries JSON
        │
        ▼
2. INSERT into query_sets — status='PENDING_REVIEW'
        │
        ▼
3. Admin reviews (manual RDS query in POC; admin UI in future)
        │
        ▼
4. Admin sets status='ACTIVE', activated_at, activated_by
   Previous ACTIVE version for same agent+templateType → status='SUPERSEDED'
        │
        ▼
5. Agents fetch queries WHERE status='ACTIVE'
```

**POC:** admin activates manually via RDS UPDATE — no review UI needed.  
**Future:** admin notification via SNS, admin UI to review and approve generated queries.

---

### RDS Tables for Data Pipeline

#### document_sources

Stable config table — one row per policy document source. Rarely changes. Adding a new source or disabling an existing one is a data operation, not a deployment.

```sql
CREATE TABLE document_sources (
    source_id      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type    VARCHAR(50)  NOT NULL,          -- 'sharepoint', 'confluence', 'web'
    source_url     TEXT         NOT NULL,
    filename       VARCHAR(255) NOT NULL,
    description    TEXT,
    agent_type     VARCHAR(50)  NOT NULL,
    template_type  VARCHAR(50)  NOT NULL,
    status         VARCHAR(50)  NOT NULL DEFAULT 'ACTIVE',
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_document_sources_lookup ON document_sources(agent_type, template_type, status);
```

| Column | Notes |
|--------|-------|
| `source_type` | `'sharepoint'`, `'confluence'`, `'web'` — drives which adapter the Lambda uses |
| `source_url` | Full URL the Lambda fetches from — works for any source type |
| `filename` | Display name used in queries JSON and resultMd |
| `description` | Human-readable note on what this document covers — admin reference only |
| `agent_type` | `security`, `data`, `risk`, `ea`, `solution` |
| `template_type` | e.g. `SDA` — matches `templateType` in documents table |
| `status` | `ACTIVE` (Lambda processes) or `INACTIVE` (skip without deleting) |

---

#### source_fetch_log

Operational history — one row per Lambda run per source. Separates transient run state from stable config. Enables skip logic, failure tracking, and traceability from fetch run to generated query set.

```sql
CREATE TABLE source_fetch_log (
    fetch_id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id             UUID         NOT NULL REFERENCES document_sources(source_id),
    fetched_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    document_modified_at  TIMESTAMPTZ,
    status                VARCHAR(50)  NOT NULL,   -- 'SUCCESS', 'SKIPPED', 'FAILED'
    skip_reason           TEXT,
    error_message         TEXT,
    query_set_id          UUID         REFERENCES query_sets(query_set_id)
);

CREATE INDEX idx_source_fetch_log_source ON source_fetch_log(source_id, fetched_at DESC);
```

| Column | Notes |
|--------|-------|
| `document_modified_at` | `last_modified` date returned by the source (SharePoint, Confluence, etc.) — used for skip comparison |
| `status` | `SUCCESS` (queries generated), `SKIPPED` (no change), `FAILED` (error) |
| `skip_reason` | Populated on `SKIPPED` — e.g. `"No change since last fetch"` |
| `error_message` | Populated on `FAILED` — full error detail for debugging |
| `query_set_id` | FK to the query set generated on `SUCCESS` — links fetch run to activated queries |

**Skip logic:**
```python
last_log = db.fetch_latest_success_log(source_id)       # latest SUCCESS row
current_modified = adapter.get_last_modified(source_url) # from source API

if last_log and current_modified <= last_log.document_modified_at:
    db.insert_fetch_log(source_id, status='SKIPPED',
                        skip_reason='No change since last fetch',
                        document_modified_at=current_modified)
    continue
```

---

#### query_sets

Stores the generated queries JSON per agent per templateType, with version history and review state.

```sql
CREATE TABLE query_sets (
    query_set_id   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id      UUID         NOT NULL REFERENCES document_sources(source_id),
    agent_type     VARCHAR(50)  NOT NULL,
    template_type  VARCHAR(50)  NOT NULL,
    version        DATE         NOT NULL,
    status         VARCHAR(50)  NOT NULL DEFAULT 'PENDING_REVIEW',
    queries_json   JSONB        NOT NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    activated_at   TIMESTAMPTZ,
    activated_by   UUID         REFERENCES users(user_id)
);

CREATE INDEX idx_query_sets_lookup ON query_sets(agent_type, template_type, status);
```

| Column | Notes |
|--------|-------|
| `version` | Date the queries JSON was generated — for traceability |
| `status` | `PENDING_REVIEW` → `ACTIVE` → `SUPERSEDED` |
| `queries_json` | Full queries JSON document as JSONB — queryable structure |
| `activated_by` | The admin user who approved this query set (NULL until activated) |

**Status lifecycle:**

```
PENDING_REVIEW  ← newly generated, awaiting admin review
      │
      ▼ (admin activates)
   ACTIVE        ← consumed by agents; only one ACTIVE set per agent+templateType
      │
      ▼ (new version activated)
SUPERSEDED       ← retained for history; no longer consumed
```

---

### Agent Query Fetching and Caching

Agents are self-contained in resolving their queries — Orchestrator passes only `agentType` and `templateType` in the task message; the agent does the rest.

**Fetch key:** `{agentType}_{templateType}` — resolved from task message fields.  
**Cache:** in-memory dict, TTL 6 hours, keyed by `{agentType}_{templateType}`.

```python
@dataclass
class CachedQuerySet:
    query_set: QuerySet
    expires_at: datetime

cache: dict[str, CachedQuerySet] = {}

def get_queries(agent_type: str, template_type: str) -> QuerySet:
    cache_key = f"{agent_type}_{template_type}"
    cached = cache.get(cache_key)
    if cached and datetime.utcnow() < cached.expires_at:
        return cached.query_set
    query_set = db.fetch_active_queries(agent_type, template_type)
    cache[cache_key] = CachedQuerySet(
        query_set=query_set,
        expires_at=datetime.utcnow() + timedelta(hours=6),
    )
    return query_set
```

**Cache behaviour:**
- On first request (cold start) or TTL expiry: fetch from RDS — `SELECT queries_json FROM query_sets WHERE agent_type=? AND template_type=? AND status='ACTIVE'`
- 6-hour TTL means agents automatically pick up newly activated query sets within 6 hours — no redeployment needed
- Cache is per ECS task instance — multiple Agent POD tasks each maintain their own cache (acceptable for POC)

---

### Storage — POC vs Production

| Storage | POC | Production upgrade |
|---------|-----|--------------------|
| queries JSON | JSONB in `query_sets.queries_json` (RDS) | S3: `query-sets/{templateType}/{agentType}/{version}.json`; `query_sets` table retains metadata + `s3_key` replacing `queries_json` |
| Why upgrade | RDS JSONB is simple and sufficient for small query sets | S3 when query sets grow large, need versioned file history, or multiple templates per agent |

For POC, JSONB in RDS is the correct choice — simpler, no S3 management overhead.

---

### Updated Full DDL (additions for Data Pipeline)

Append after `agent_tasks` and `audit_logs` in the schema:

```sql
CREATE TABLE document_sources (
    source_id      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type    VARCHAR(50)  NOT NULL,
    source_url     TEXT         NOT NULL,
    filename       VARCHAR(255) NOT NULL,
    description    TEXT,
    agent_type     VARCHAR(50)  NOT NULL,
    template_type  VARCHAR(50)  NOT NULL,
    status         VARCHAR(50)  NOT NULL DEFAULT 'ACTIVE',
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_document_sources_lookup ON document_sources(agent_type, template_type, status);

CREATE TABLE query_sets (
    query_set_id   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id      UUID         NOT NULL REFERENCES document_sources(source_id),
    agent_type     VARCHAR(50)  NOT NULL,
    template_type  VARCHAR(50)  NOT NULL,
    version        DATE         NOT NULL,
    status         VARCHAR(50)  NOT NULL DEFAULT 'PENDING_REVIEW',
    queries_json   JSONB        NOT NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    activated_at   TIMESTAMPTZ,
    activated_by   UUID         REFERENCES users(user_id)
);

CREATE INDEX idx_query_sets_lookup ON query_sets(agent_type, template_type, status);

CREATE TABLE source_fetch_log (
    fetch_id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id             UUID         NOT NULL REFERENCES document_sources(source_id),
    fetched_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    document_modified_at  TIMESTAMPTZ,
    status                VARCHAR(50)  NOT NULL,
    skip_reason           TEXT,
    error_message         TEXT,
    query_set_id          UUID         REFERENCES query_sets(query_set_id)
);

CREATE INDEX idx_source_fetch_log_source ON source_fetch_log(source_id, fetched_at DESC);
```

---

## Relay Service — SQS Consumer Design

### What it is

A **plain asyncio worker process** running inside the Agent POD ECS container. No web framework for the consumer loop. The only HTTP server in the container is a minimal health endpoint on `:8080` used exclusively by ECS health checks.

---

### Module Structure

```
relay-service/
├── main.py           ← entry point: starts poll loop + health server concurrently
├── worker.py         ← SQS polling loop, concurrency control, message lifecycle
├── dispatcher.py     ← lazy handler registry (agentType → handler instance)
├── publisher.py      ← publishes AgentResult to aia-status
└── handlers/
    ├── base.py       ← AgentHandler protocol (interface only)
    ├── security.py
    ├── data.py
    ├── risk.py
    ├── ea.py
    └── solution.py
```

---

### SQS Polling Loop

```python
# worker.py
MAX_CONCURRENT = 5  # configurable — tune after load testing

async def poll_loop():
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    while True:
        response = await sqs.receive_message(
            QueueUrl=TASKS_QUEUE_URL,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,       # long polling
        )
        for msg in response.get("Messages", []):
            asyncio.create_task(_process(semaphore, msg))

async def _process(semaphore: asyncio.Semaphore, msg: dict):
    async with semaphore:
        task = TaskMessage.model_validate_json(msg["Body"])
        try:
            handler = dispatcher.get(task.agent_type)
            result = await handler.process(task)
            await publisher.publish_success(result)
        except Exception as exc:
            await publisher.publish_failure(task, error=str(exc))
        finally:
            await sqs.delete_message(
                QueueUrl=TASKS_QUEUE_URL,
                ReceiptHandle=msg["ReceiptHandle"],
            )
```

**Concurrency model:**
- `asyncio.Semaphore(MAX_CONCURRENT)` caps concurrent Bedrock calls — default 5, configurable via env var
- Each task runs in its own `asyncio.create_task` — polling continues while tasks are in flight
- `delete_message` is in `finally` — message deleted on both success and failure; errors are surfaced via the status message, not SQS retry
- If the ECS task crashes before `finally`, visibility timeout expires → SQS re-delivers → after `maxReceiveCount=3` → moves to `aia-tasks-dlq`

---

### Who Publishes to aia-status

**`worker.py` (the service layer) owns all SQS I/O** — not the individual handlers.

```
worker.py receives message from aia-tasks
    │
    ▼
dispatcher.get(agentType) → handler.process(task) → AgentResult
    │
    ▼
publisher.publish_success(result)   ← on success
    OR
publisher.publish_failure(task, error)  ← on exception
    │
    ▼
aia-status queue (consumed by Orchestrator)
    │
    ▼
worker.py deletes original message from aia-tasks
```

Individual handlers (`SecurityHandler`, `DataHandler`, etc.) are **pure** — receive a `TaskMessage`, call Bedrock, return an `AgentResult`. They have no knowledge of SQS. This keeps them independently testable and reusable outside the queue context.

---

### Lazy Handler Instantiation

Handlers are instantiated **on first use**, not at startup. An Agent POD that only ever receives `security` and `data` tasks never loads `EaHandler` or `SolutionHandler`.

```python
# dispatcher.py
_registry: dict[str, type[AgentHandler]] = {
    "security": SecurityHandler,
    "data":     DataHandler,
    "risk":     RiskHandler,
    "ea":       EaHandler,
    "solution": SolutionHandler,
}
_live: dict[str, AgentHandler] = {}

def get(agent_type: str) -> AgentHandler:
    if agent_type not in _live:
        handler_class = _registry.get(agent_type)
        if not handler_class:
            raise ValueError(f"Unknown agent_type: {agent_type}")
        _live[agent_type] = handler_class()
    return _live[agent_type]
```

Once instantiated, the handler instance is **reused** for all subsequent messages of that type — no repeated construction cost.

---

### Dynamic Agent Selection by templateType — Option A (decided)

**Orchestrator pre-filters active agents at dispatch time.** Only tasks for agents that have an `ACTIVE` query set for the given `templateType` are published to `aia-tasks`. The expected fan-in set is dynamic — not hardcoded to 5.

```python
# Orchestrator — at dispatch time
active_agent_types = db.fetch_active_agent_types(template_type)
# SELECT DISTINCT agent_type FROM query_sets
# WHERE template_type = ? AND status = 'ACTIVE'
#
# e.g. SDA template, all 5 agents configured → ['security', 'data', 'risk', 'ea', 'solution']
# e.g. lighter template, 3 agents configured  → ['security', 'data', 'risk']

expected_task_ids = {f"{document_id}_{a}" for a in active_agent_types}
# fan-in waits for exactly this set
```

**Why Option A over Option B:**

| | Option A (Orchestrator pre-filters) | Option B (Agent returns SKIPPED) |
|-|-------------------------------------|----------------------------------|
| Messages on queue | Only applicable tasks published | Always 5 tasks regardless of template |
| Fan-in set | Dynamic — built from query_sets at dispatch | Static 5, minus SKIPPED responses |
| Relay Service | No change needed | Needs SKIPPED status + extra RDS check per task |
| New status needed | No | Yes — SKIPPED propagates everywhere |
| Orchestrator RDS access | One query at dispatch time (agent list only, not query content) | None |

Orchestrator only queries *which* agent types are active for the template — it does not see the queries content. This is consistent with the principle that Orchestrator has no visibility into query details.

**Behaviour when a new templateType is added:**
1. Data Pipeline generates query sets for the new template
2. Admin activates them in `query_sets`
3. Next upload with that `templateType` → Orchestrator picks up the active agents automatically
4. No code deployment needed

---

## Agent Dynamically Deciding

### How an Agent Resolves its Queries

Each agent handler is self-contained in resolving its queries. The `TaskMessage` carries `agentType` and `templateType` — this is the full lookup key. The agent fetches its queries from RDS and caches them in memory.

---

### In-Memory Cache

```python
# handlers/base.py (shared by all handlers)
@dataclass
class CachedQuerySet:
    query_set: QuerySet
    expires_at: datetime

_cache: dict[str, CachedQuerySet] = {}

def get_queries(agent_type: str, template_type: str) -> QuerySet:
    cache_key = f"{agent_type}_{template_type}"
    cached = _cache.get(cache_key)
    if cached and datetime.utcnow() < cached.expires_at:
        return cached.query_set
    # cache miss — fetch from RDS
    query_set = db.fetch_active_queries(agent_type, template_type)
    _cache[cache_key] = CachedQuerySet(
        query_set=query_set,
        expires_at=datetime.utcnow() + timedelta(hours=6),
    )
    return query_set
```

**RDS query on cache miss:**
```sql
SELECT queries_json
FROM query_sets
WHERE agent_type = ?
  AND template_type = ?
  AND status = 'ACTIVE'
LIMIT 1;
```

**Cache behaviour:**

| Scenario | Behaviour |
|----------|-----------|
| Cold start (first message) | Fetch from RDS, populate cache |
| Within 6-hour TTL | Serve from cache — no RDS hit |
| TTL expired | Re-fetch from RDS — picks up any newly activated query sets |
| New query set activated | Agents pick it up within 6 hours — no redeployment needed |
| Cache is per ECS task | Multiple Agent POD instances each maintain their own cache — acceptable for POC |

---

### Handler Processing Flow

```python
# handlers/security.py
class SecurityHandler:
    async def process(self, task: TaskMessage) -> AgentResult:
        # 1. Resolve queries (cache-first)
        queries = get_queries(task.agent_type, task.template_type)

        # 2. Resolve document content (inline or S3 fallback)
        content = task.file_content or await fetch_from_s3(task.s3_bucket, task.s3_key)

        # 3. Build prompt — queries + document content passed to Bedrock
        prompt = build_prompt(queries, content)

        # 4. Call Bedrock
        raw_response = await model_client.invoke(prompt, system=SYSTEM_PROMPT)

        # 5. Parse and return structured result
        return parse_agent_result(raw_response, queries, task.agent_type)
```

The handler does not know about SQS, RDS writes, or the status queue — those are entirely owned by `worker.py` and `publisher.py`.

---

## resultMd Generation Logic

### Inputs and Output

| | Type | Source |
|-|------|--------|
| **Input** | `list[AgentResult]` | Collected by Orchestrator from `aia-status` queue |
| **Input** | `document_filename` | `documents.original_filename` |
| **Input** | `missing_agents` | Agent types that did not respond within threshold (empty list on COMPLETE) |
| **Output** | `str` | Complete markdown string → written to `documents.result_md` |

---

### Constants (from config)

```python
AGENT_ORDER = ["security", "data", "risk", "ea", "solution"]

SECTION_TITLES = {
    "security": "Security Policy",
    "data":     "Data Policy",
    "risk":     "Risk & Compliance Policy",
    "ea":       "Enterprise Architecture",
    "solution": "Solution Designs",
}

RATING_EMOJI = {
    "GREEN": "🟢 Green",
    "AMBER": "🟡 Amber",
    "RED":   "🔴 Red",
}

NARRATIVE = {
    "GREEN": "The submitted document demonstrates strong alignment across all assessed domains.",
    "AMBER": "The submitted document shows partial alignment. {n} domain(s) have areas requiring attention.",
    "RED":   "The submitted document has critical gaps in {n} domain(s) that must be addressed before approval.",
}

CONCLUSION = {
    "GREEN": "🟢 GREEN — Document meets all assessed criteria.",
    "AMBER": "🟡 AMBER — Partial alignment; {n} action(s) required.",
    "RED":   "🔴 RED — Critical gaps identified; see Priority Actions above.",
}
```

---

### Algorithm

#### Step 1 — Sort results into canonical order

```python
result_map = {r.agent_type: r for r in agent_results}
ordered = [result_map[a] for a in AGENT_ORDER if a in result_map]
```

Sections always appear in the same sequence regardless of response arrival order. Missing agents produce no section.

---

#### Step 2 — For each agent result, render one section block

```python
def render_section(result: AgentResult) -> str:
    title   = SECTION_TITLES[result.agent_type]
    red     = sum(1 for a in result.assessments if a.rating == "RED")
    amber   = sum(1 for a in result.assessments if a.rating == "AMBER")
    green   = sum(1 for a in result.assessments if a.rating == "GREEN")

    rows = "\n".join(
        f"| {a.question} | {RATING_EMOJI[a.rating]} | {a.comments} | {a.section} |"
        for a in result.assessments
    )

    return f"""## {title}
**Reference:** [{result.source_document.filename}]({result.source_document.url})

| Question / Query | Rating | Comments | Section |
|------------------|--------|----------|---------|
{rows}

**Summary: {red} Red, {amber} Amber, {green} Green**

---
"""
```

Rating counts are derived directly from `assessments[].rating` — the summary string on the AgentResult is not parsed.

---

#### Step 3 — Build Cross-Category Scorecard

```python
def agent_overall(result: AgentResult) -> str:
    ratings = {a.rating for a in result.assessments}
    if "RED"   in ratings: return "🔴 Red"
    if "AMBER" in ratings: return "🟡 Amber"
    return "🟢 Green"

def render_scorecard(ordered: list[AgentResult]) -> str:
    rows = "\n".join(
        "| {title} | {red} | {amber} | {green} | {overall} |".format(
            title   = SECTION_TITLES[r.agent_type],
            red     = sum(1 for a in r.assessments if a.rating == "RED"),
            amber   = sum(1 for a in r.assessments if a.rating == "AMBER"),
            green   = sum(1 for a in r.assessments if a.rating == "GREEN"),
            overall = agent_overall(r),
        )
        for r in ordered
    )
    return f"""### Cross-Category Scorecard
| Category | Red | Amber | Green | Overall |
|----------|-----|-------|-------|---------|
{rows}
"""
```

---

#### Step 4 — Extract Priority Actions

Walk all assessments in canonical agent order. Collect every `rating == "RED"` item, numbered sequentially.

```python
def render_priority_actions(ordered: list[AgentResult]) -> str:
    actions = [
        f"{i+1}. {a.comments} *({SECTION_TITLES[r.agent_type]})*"
        for i, (r, a) in enumerate(
            (r, a)
            for r in ordered
            for a in r.assessments
            if a.rating == "RED"
        )
    ]
    body = "\n".join(actions) if actions else "_No critical issues identified._"
    return f"### Priority Actions\n{body}\n"
```

---

#### Step 5 — Calculate Overall Document Rating

```python
def overall_rating(ordered: list[AgentResult]) -> str:
    all_ratings = {a.rating for r in ordered for a in r.assessments}
    if "RED"   in all_ratings: return "RED"
    if "AMBER" in all_ratings: return "AMBER"
    return "GREEN"
```

Calculated from responding agents only — missing agents (PARTIAL_COMPLETE) do not contribute.

---

#### Step 6 — Render Full Evaluation Summary

```python
def render_summary_section(ordered: list[AgentResult]) -> str:
    rating  = overall_rating(ordered)
    n_amber = sum(1 for r in ordered if agent_overall(r) in ("🟡 Amber", "🔴 Red"))
    n_red_actions = sum(
        1 for r in ordered for a in r.assessments if a.rating == "RED"
    )

    narrative  = NARRATIVE[rating].format(n=n_amber)
    conclusion = CONCLUSION[rating].format(n=n_red_actions)

    return f"""## Full Evaluation Summary

{narrative}

{render_scorecard(ordered)}
{render_priority_actions(ordered)}
### Overall Conclusion
**Overall: {conclusion}**
"""
```

---

#### Step 7 — Prepend PARTIAL_COMPLETE Warning Banner

```python
def render_warning_banner(missing_agents: list[str]) -> str:
    if not missing_agents:
        return ""
    names = ", ".join(SECTION_TITLES[a] for a in missing_agents)
    return f"""⚠ **Partial Assessment:** the following domains did not complete \
within the processing threshold: {names}.
Results below reflect responding domains only.

---
"""
```

---

#### Step 8 — Assemble Final String

```python
def generate(
    agent_results:  list[AgentResult],
    document_filename: str,
    missing_agents: list[str],
) -> str:
    ordered = [
        result_map[a]
        for a in AGENT_ORDER
        if a in (result_map := {r.agent_type: r for r in agent_results})
    ]

    sections = "\n".join(render_section(r) for r in ordered)

    return (
        f"# {document_filename} Evaluation\n\n"
        + render_warning_banner(missing_agents)
        + sections
        + render_summary_section(ordered)
    )
```

---

### Complete resultMd Example

```markdown
# architecture-v2.pdf Evaluation

## Security Policy
**Reference:** [Security-Control-Matrix.docx](https://sharepoint.com/sites/policies/Security-Control-Matrix.docx)

| Question / Query | Rating | Comments | Section |
|------------------|--------|----------|---------|
| Are authentication and authorization controls defined? | 🟢 Green | RBAC and MFA documented. | Section 2.1, Page 6 |
| Is incident response end-to-end and time-bound? | 🟡 Amber | Steps documented but SLA mapping is weak. | Slide 14 |

**Summary: 0 Red, 1 Amber, 1 Green**

---

## Data Policy
**Reference:** [Data-Governance-Framework.docx](https://sharepoint.com/sites/policies/Data-Governance-Framework.docx)

| Question / Query | Rating | Comments | Section |
|------------------|--------|----------|---------|
| Is a DSAR process defined and rehearsed? | 🔴 Red | No DSAR rehearsal evidence found. | Section 4.2, Page 18 |
| Is data retention policy documented? | 🟡 Amber | Policy exists but lacks enforcement detail. | Section 3.1, Page 12 |

**Summary: 1 Red, 1 Amber, 0 Green**

---

## Full Evaluation Summary

The submitted document has critical gaps in 1 domain(s) that must be addressed before approval.

### Cross-Category Scorecard
| Category | Red | Amber | Green | Overall |
|----------|-----|-------|-------|---------|
| Security Policy | 0 | 1 | 1 | 🟡 Amber |
| Data Policy     | 1 | 1 | 0 | 🔴 Red   |

### Priority Actions
1. No DSAR rehearsal evidence found. *(Data Policy)*

### Overall Conclusion
**Overall: 🔴 RED — Critical gaps identified; see Priority Actions above.**
```

---

## Frontend

### Stack (existing)

| Concern | Technology |
|---------|-----------|
| Server | Hapi.js 21 — server-side rendered, not a SPA |
| Templates | Nunjucks |
| Styling | GOV.UK Frontend v6 (SCSS + macros) |
| Client JS | Plain ES modules bundled via Webpack 5 |
| Markdown | `marked` v17 — already integrated, GOV.UK table classes applied post-render |
| Auth gate | UUID-based session via `@hapi/yar` (maps to GuestMode exactly) |
| Session store | Memory (dev) / Redis (prod) |
| HTTP client | `node-fetch` — all backend calls made server-side from Hapi controllers |

---

### What Exists and Aligns

| Feature | File | Notes |
|---------|------|-------|
| Upload form | `src/server/home/` | File picker, DOCX/PDF validation, size limit |
| History list | `src/server/home/`, `src/server/dashboard/` | Paginated, calls backend history API |
| Result page | `src/server/result/` | Fetches markdown from backend, renders via `marked` |
| Markdown renderer | `src/client/javascripts/markdown-handler.js` | GOV.UK table classes applied post-render |
| Auth gate | `src/server/common/helpers/auth-guard.js` | UUID session — matches GuestMode design |
| Backend headers | `src/server/common/helpers/backend-headers.js` | JWT + X-User-Id headers on all backend calls |

---

### API Layers — Three Distinct Layers

The frontend involves three separate communication layers. It is important not to confuse them.

---

#### Layer 1 — Browser → Hapi (page navigation, unchanged)

These are Hapi's own page routes. The browser navigates to these. They do not change and are not CoreBackend paths.

| Method | Hapi route | Purpose |
|--------|-----------|---------|
| GET | `/` | Home / history page |
| POST | `/upload` | Upload form submission |
| GET | `/result` | Result display page |
| GET | `/access` | UUID auth gate |
| GET | `/dashboard` | Protected dashboard |
| GET | `/health` | Hapi health check |

---

#### Layer 2 — Hapi → CoreBackend (server-side, internal, never seen by browser)

These are the calls Hapi controllers make to CoreBackend from the server. **These are the final CoreBackend paths** — authoritative, as designed in the CoreBackend Services section. The browser never calls these directly.

| Purpose | Final CoreBackend path | Existing Hapi code calls | Change needed |
|---------|----------------------|--------------------------|---------------|
| Upload document | `POST /documents/upload` | `POST /api/upload` | Update path + add `templateType` |
| Fetch history list | `GET /documents?page=&limit=` | `GET /api/fetchUploadHistory` | Update path + response shape |
| Fetch result detail | `GET /documents/{documentId}` | `GET RESULT_API_URL?docID=x` | Update path + read `.resultMd` |
| Check document status | `GET /documents/{documentId}/status` | _(not implemented)_ | New |
| Fetch template types | `GET /templates` | _(not implemented)_ | New (new CoreBackend endpoint too) |

**Result controller fix:** `result/controller.js` currently searches for `.markdownContent`, `.markdown`, `.content[0].text` in the API response. Update to read `.resultMd` from the `GET /documents/{documentId}` response shape.

---

#### Layer 3 — Browser JS → Hapi (AJAX/JSON, thin proxy routes)

These are new lightweight Hapi routes that the browser's client-side JavaScript calls for polling and template fetching. They exist only because the Hapi server acts as a BFF — the browser never calls CoreBackend directly (no CORS required on CoreBackend).

Required only if **Option A** is confirmed for polling:

| Browser JS calls | Hapi proxies to CoreBackend | Notes |
|------------------|-----------------------------|-------|
| `GET /api/status/{documentId}` | `GET /documents/{documentId}/status` | Called every 30s per in-progress document |
| `GET /api/templates` | `GET /templates` | Called once on page load; Hapi caches 24h |

If **Option B** (browser calls CoreBackend directly) is confirmed, Layer 3 is not needed — but CoreBackend ALB must have CORS enabled and auth headers must be available client-side.

---

### New CoreBackend Endpoint — GET /templates

A new endpoint is needed to serve available `templateType` values to the frontend.

```
GET /templates
Authorization: resolved by AuthMiddleware
```

**Implementation:** queries `query_sets` for distinct active template types:
```sql
SELECT DISTINCT template_type FROM query_sets WHERE status = 'ACTIVE' ORDER BY template_type;
```

**Response 200:**
```json
{
  "templates": ["SDA"]
}
```

This endpoint is added to the CoreBackend Endpoint Summary table (see CoreBackend Services section).

---

### templateType Selector on Upload Form

The upload form needs a dropdown for `templateType`. The Hapi server fetches available values from `GET /templates` on CoreBackend and caches the result for **24 hours** in memory.

```javascript
// Hapi server — templateType cache
let templateCache = { values: [], expiresAt: 0 }

async function getTemplateTypes(request) {
  if (Date.now() < templateCache.expiresAt) return templateCache.values
  const res = await fetch(`${BACKEND_API_URL}/templates`, {
    headers: buildBackendHeaders(request)
  })
  const { templates } = await res.json()
  templateCache = { values: templates, expiresAt: Date.now() + 86_400_000 } // 24h
  return templates
}
```

On the upload form, the dropdown is rendered server-side by the Hapi controller passing `templateTypes` to the Nunjucks template. If the cache fetch fails, the form falls back to a hardcoded default (`['SDA']`).

---

### Upload Flow

```
1. User selects file + templateType → clicks Submit
        │
        ▼
2. Client-side JS shows progress bar (tracks multipart upload progress)
        │
        ▼
3. Hapi server POSTs to CoreBackend POST /documents/upload
   CoreBackend: S3 upload → RDS INSERT (UPLOADING → UPLOADED → PENDING) → fires Orchestrator
   Returns: 202 { documentId, status: PENDING }
        │
        ▼
4. Progress bar reaches 100% — upload complete
        │
        ▼
5. Hapi redirects back to home/dashboard page
   Page re-fetches history → GET /documents?page=1&limit=20
   New document record appears at top of history table with status PENDING or PROCESSING
```

**Progress bar implementation:** client-side `XMLHttpRequest` (or `fetch` with `ReadableStream`) tracks upload bytes sent vs total. GOV.UK progress component or custom SCSS bar. No server-side streaming needed — standard browser upload progress event.

---

### History Table — Status Display

The history table shows each document's current status. Status values from our ENUM map to UI labels:

| Status | UI label | Badge colour (GOV.UK) |
|--------|----------|----------------------|
| `UPLOADING` | Processing | Blue |
| `UPLOADED` | Processing | Blue |
| `PENDING` | Processing | Blue |
| `PROCESSING` | Processing | Blue |
| `COMPLETE` | Completed | Green |
| `PARTIAL_COMPLETE` | Partial | Yellow |
| `ERROR` | Failed | Red |

Non-terminal statuses (`UPLOADING`, `UPLOADED`, `PENDING`, `PROCESSING`) all show the same "Processing" badge — consistent with the frontend rule that non-terminal states look identical to the user.

**"View Result" button:** rendered only for `COMPLETE` and `PARTIAL_COMPLETE` rows. Clicking calls `GET /documents/{documentId}` via Hapi → renders the result page with `resultMd`.

---

### Polling — Two Options (to be confirmed)

Since the upload redirects back to the history page and the new document record is visible immediately as "Processing", the user needs a way to see when it completes without manually refreshing.

#### Option A — Client-side JS polls a Hapi proxy endpoint (recommended)

Browser JS checks if any documents on the current page are in a non-terminal status. If so, polls `GET /api/status/{documentId}` on the Hapi server every 30s for each such document. Hapi proxy calls `GET /documents/{documentId}/status` on CoreBackend and returns JSON to the browser. On terminal status, the JS updates the table row badge and shows/hides the "View Result" button without a full page reload.

```javascript
// client-side — polling for in-progress documents
const POLL_INTERVAL_MS = 30_000
const TERMINAL = new Set(['COMPLETE', 'PARTIAL_COMPLETE', 'ERROR'])

function pollInProgress() {
  const rows = document.querySelectorAll('[data-doc-id][data-status="PROCESSING"]')
  rows.forEach(row => {
    const docId = row.dataset.docId
    const interval = setInterval(async () => {
      const res = await fetch(`/api/status/${docId}`)
      const { status } = await res.json()
      if (TERMINAL.has(status)) {
        clearInterval(interval)
        updateRow(row, status)  // update badge, show "View Result" button
      }
    }, POLL_INTERVAL_MS)
  })
}
```

New Hapi route needed: `GET /api/status/{documentId}` → proxies to `GET /documents/{documentId}/status` on CoreBackend.

**Pros:** consistent with existing pattern (all backend calls through Hapi), no CORS config on CoreBackend, auth headers added server-side.  
**Cons:** extra Hapi → CoreBackend hop per poll.

---

#### Option B — Client-side JS calls CoreBackend directly

Browser JS polls the CoreBackend ALB URL directly — no Hapi proxy hop.

**Pros:** simpler, one fewer hop.  
**Cons:** requires CORS config on CoreBackend ALB, exposes backend URL in browser, auth headers must be available client-side.

---

*Team to confirm Option A or B. Option A is recommended for POC — consistent with existing architecture and no CORS changes required.*

---

### PARTIAL_COMPLETE Rendering

The `resultMd` already contains the warning banner (prepended by DeterministicSummary). Since the result page renders the full `resultMd` via `marked`, the banner renders automatically as markdown.

The GOV.UK warning component style can be applied post-render in `markdown-handler.js` — detect the `⚠` prefix line and wrap it in a `govuk-warning-text` div.

---

### Summary of Frontend Changes Required

| Change | File | Type |
|--------|------|------|
| Add `GET /api/templates` Hapi proxy route + 24h cache | `src/server/home/` | New |
| Add `templateType` dropdown to upload form, populated from cache | `src/server/home/index.njk` | Modify |
| Update upload controller — send `templateType` in FormData to new endpoint | `src/server/home/controller.js` | Modify |
| Update history controller — call `GET /documents` endpoint, map status values | `src/server/home/controller.js` | Modify |
| Update result controller — read `.resultMd` from `GET /documents/{documentId}` | `src/server/result/controller.js` | Modify |
| Add upload progress bar (client-side XHR progress event) | `src/client/javascripts/upload-handler.js` | Modify |
| Add client-side polling for in-progress history rows | `src/client/javascripts/` | New |
| Add `GET /api/status/{documentId}` Hapi proxy route | `src/server/` | New (Option A only) |
| Map 7 status ENUM values to GOV.UK badge colours in history template | `src/server/home/index.njk` | Modify |
| PARTIAL_COMPLETE warning banner styling post-render | `src/client/javascripts/markdown-handler.js` | Modify |

---

## Notes on Current Architecture (for reference)

The existing system uses:
- **Lambda** (9 stages, choreography via EventBridge)
- **Redis** (ElastiCache) for transient pipeline state
- **SQS** for agent task dispatch
- **Anthropic SDK** (not Bedrock)
- **PostgreSQL** for final results

The proposed ECS Fargate design moves toward long-running services and replaces Lambda + EventBridge with a simpler SNS/SQS-based request/response pattern.
