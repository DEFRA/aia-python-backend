# AWS Event-Driven Orchestration — Document Processing Pipeline

End-to-end architecture using SQS, EventBridge, CloudWatch, and Redis Cache (ElastiCache).

---

## Core Pattern: Choreography via EventBridge

Instead of a central orchestrator, each stage publishes a completion event to a custom **EventBridge event bus**. EventBridge rules route each event to the next stage's Lambda. Stages are fully decoupled — they only know their own input event and output event.

**Custom event bus:** `defra-pipeline`

**Event envelope:**
```json
{
  "source": "defra.pipeline",
  "detail-type": "DocumentParsed",
  "detail": {
    "docId": "UUID-1234",
    "s3Key": "in_progress/UUID-1234.pdf",
    "timestamp": "2026-03-27T10:00:00Z"
  }
}
```

---

## Full Event Flow

```
S3 upload
  → EventBridge (Object Created)
    → SQS FIFO
      → Parse Lambda        publishes: DocumentParsed
        → Tagging Lambda    publishes: DocumentTagged
          → Extract Lambda  publishes: SectionsReady ×5
            → Agent ×5      each publishes: AgentComplete
                            last one publishes: AllAgentsComplete
              → Compile      publishes: DocumentCompiled
                → Persist   publishes: ResultPersisted   ─┐
                → S3 Move   publishes: DocumentMoved     ─┤ both → FinaliseReady
                                                           ↓
                                                     Notify + SQS.delete
                                                           ↓
                                                     PipelineComplete
```

- **Redis** holds intermediate state between every stage
- **EventBridge** replaces all direct Lambda-to-Lambda calls
- **CloudWatch** observes every transition
- **SQS** anchors durability and ordering at the entry point

---

## Stage-by-Stage Breakdown

### Stage 2 — Upload Detection

```
S3 in_progress/  →  S3 EventBridge integration (native, no Lambda needed)
                 →  EventBridge rule: detail-type = "Object Created"
                 →  SQS FIFO Queue  (MessageGroupId = "pipeline")
                 →  Parse Lambda (polls SQS)
```

- S3 natively sends events to EventBridge — no SNS/notification config required
- SQS FIFO preserves **earliest-timestamp-first** ordering
- The SQS **receipt handle** is captured by the Parse Lambda and stored in Redis for the full pipeline duration — the message stays invisible until Stage 9 explicitly deletes it
- A **Dead Letter Queue (DLQ)** on the FIFO queue catches documents that fail after N retries

**CloudWatch alarm:** `DLQ depth > 0` → page on-call

---

### Stage 3 — Parse (PDF / DOCX)

Parse Lambda runs `extract_text_blocks()` + `clean_and_chunk()` for PDFs, or `python-docx` paragraph iteration for `.docx` files. Both paths produce the same chunk schema:

```json
{ "chunk_index", "page", "is_heading", "char_count", "text" }
```

For `.docx`, paragraph style name (`Heading 1`, `Heading 2`, `Normal`) replaces font-size heuristics for `is_heading`. Section/paragraph index is used as a proxy for `page`.

For scanned PDFs with no extractable text layer, fall back to **Amazon Textract** or Claude vision before chunking.

**Redis** caches parsed chunks keyed by content hash — not filename — so resubmissions skip the parse entirely:

```
KEY:   chunks:{sha256(file_bytes)}
TTL:   24 hours
VALUE: JSON array of chunk dicts
```

On success, Lambda stores the SQS receipt handle in Redis and publishes to EventBridge:

```json
{
  "detail-type": "DocumentParsed",
  "detail": { "docId": "...", "chunksCacheKey": "chunks:abc123" }
}
```

**CloudWatch:** Lambda duration metric + error rate alarm per stage function.

---

### Stage 4 — Tagging Agent

Triggered by EventBridge rule matching `DocumentParsed`. Lambda reads chunks from Redis using `chunksCacheKey` and calls Claude with the tagging prompt and taxonomy.

**Redis** caches the tagged output — this is the most expensive LLM call in the pipeline:

```
KEY:   tagged:{sha256(file_bytes)}
TTL:   24 hours
```

If the document is resubmitted or tagging is re-run with the same content, the Claude call is skipped entirely.

Publishes:
```json
{
  "detail-type": "DocumentTagged",
  "detail": { "docId": "...", "taggedCacheKey": "tagged:abc123" }
}
```

---

### Stage 5 — Section Extraction

Triggered by `DocumentTagged`. Lambda reads tagged chunks from Redis and runs `extract_sections_for_agent()` for each of the five agent types. Each filtered payload — including the nearest preceding heading for section context — is written back to Redis:

```
KEY:   sections:{docId}:security
KEY:   sections:{docId}:data
KEY:   sections:{docId}:risk
KEY:   sections:{docId}:ea
KEY:   sections:{docId}:solution
TTL:   1 hour
```

Lambda then publishes **one event per agent** to EventBridge:

```json
{
  "detail-type": "SectionsReady",
  "detail": { "docId": "...", "agentType": "security" }
}
```

Five events fan out concurrently. Each is routed by an EventBridge rule to the same Agent Lambda with a different `agentType` — triggering all five agents in parallel.

---

### Stage 6 — Specialist Agents (Parallel)

Each `SectionsReady` event triggers the Agent Lambda independently. Each invocation:

1. Reads its sections from Redis (`sections:{docId}:{agentType}`)
2. Pulls its checklist questions from PostgreSQL via **RDS Proxy**. Questions are cached in Redis to avoid repeated DB hits:
   ```
   KEY:   questions:{agentType}
   TTL:   1 hour (invalidate on question table update)
   ```
3. Calls the Claude API with sections + questions
4. Writes its result to Redis:
   ```
   KEY:   result:{docId}:{agentType}
   TTL:   1 hour
   ```
5. Increments a Redis counter for this document:
   ```
   INCR   results_count:{docId}
   ```
6. If counter reaches 5 (all agents complete), publishes `AllAgentsComplete`
7. Always publishes `AgentComplete` (for CloudWatch tracking)

**PostgreSQL questions table:**
```sql
CREATE TABLE checklist_questions (
    id          SERIAL PRIMARY KEY,
    agent_type  TEXT NOT NULL,
    question    TEXT NOT NULL,
    active      BOOLEAN DEFAULT TRUE
);
```

**CloudWatch:** Custom metric `AgentDuration` with `agentType` dimension — identifies the slowest agent.

---

### Stage 7 — Compile Results

Triggered by `AllAgentsComplete`. Lambda reads all five results from Redis:

```
result:{docId}:security
result:{docId}:data
result:{docId}:risk
result:{docId}:ea
result:{docId}:solution
```

Assembles the final `front_end_response` JSON:

```json
{
  "docId": "UUID-1234",
  "type": "Solution Design Team",
  "generatedAt": "2026-03-27T10:00:00Z",
  "content": [{ "type": "text", "text": "...markdown tables per agent..." }],
  "status": "completed",
  "processedAt": "2026-03-27T10:05:00Z"
}
```

Publishes:
```json
{
  "detail-type": "DocumentCompiled",
  "detail": { "docId": "...", "compiledCacheKey": "compiled:UUID-1234" }
}
```

---

### Stage 8 — Persist + Move (Concurrent)

`DocumentCompiled` triggers **two** EventBridge rules simultaneously:

**Rule A → Persist Lambda:**
- Inserts compiled JSON into PostgreSQL `assessment_results` table
- Publishes `ResultPersisted`

```sql
CREATE TABLE assessment_results (
    doc_id        UUID PRIMARY KEY,
    doc_type      TEXT,
    generated_at  TIMESTAMPTZ,
    processed_at  TIMESTAMPTZ,
    status        TEXT,
    result_json   JSONB
);
```

**Rule B → S3 Move Lambda:**
- Copies object from `in_progress/` to `completed/` or `error/`
- Deletes original from `in_progress/`
- Publishes `DocumentMoved`

Both `ResultPersisted` and `DocumentMoved` must arrive before notifying the front-end. A Redis counter coordinates:

```
INCR   stage8_count:{docId}   → publishes FinaliseReady when count == 2
```

---

### Stage 9 — Notify + Pop Queue

Triggered by `FinaliseReady`. Lambda:

1. Publishes to **SNS** → front-end webhook or push event:
   ```json
   { "docId": "UUID-1234", "status": "completed" }
   ```
2. Reads the SQS receipt handle from Redis (`receipt:{docId}`) and calls `sqs.delete_message()` to remove the document from the queue
3. Cleans up all Redis keys for this `docId`
4. Publishes `PipelineComplete` — captured by CloudWatch for audit and duration tracking

**Redis receipt handle key (written at Stage 3):**
```
KEY:   receipt:{docId}
TTL:   matches SQS visibility timeout
VALUE: SQS receipt handle string
```

---

## CloudWatch: Observability Layer

| What to monitor | How |
|-----------------|-----|
| End-to-end duration per document | Log `PipelineStart` + `PipelineComplete` events; metric filter on `docId` |
| Per-stage error rates | Lambda error metric per function; alarm on threshold |
| DLQ depth | `ApproximateNumberOfMessagesVisible` alarm on DLQ → SNS alert |
| Agent latency by type | Custom metric `AgentDuration` with `agentType` dimension |
| Queue backlog | SQS `ApproximateNumberOfMessagesVisible` on main queue |
| Redis memory pressure | ElastiCache `DatabaseMemoryUsagePercentage` alarm |
| Pipeline health dashboard | CloudWatch Dashboard aggregating all of the above |

---

## Redis Key Reference

| Key | Written at | Read at | TTL |
|-----|-----------|---------|-----|
| `chunks:{content_hash}` | Stage 3 | Stage 3 (cache hit) | 24h |
| `tagged:{content_hash}` | Stage 4 | Stage 4 (cache hit) | 24h |
| `sections:{docId}:{agentType}` | Stage 5 | Stage 6 | 1h |
| `questions:{agentType}` | Stage 6 | Stage 6 (cache hit) | 1h |
| `result:{docId}:{agentType}` | Stage 6 | Stage 7 | 1h |
| `results_count:{docId}` | Stage 6 (INCR) | Stage 6 | 1h |
| `compiled:{docId}` | Stage 7 | Stage 8 | 1h |
| `stage8_count:{docId}` | Stage 8 (INCR) | Stage 8 | 30m |
| `receipt:{docId}` | Stage 3 | Stage 9 | SQS visibility timeout |

---

## EventBridge Rules Reference

| Rule | Matches | Target |
|------|---------|--------|
| `on-document-uploaded` | S3 Object Created in `in_progress/` | SQS FIFO Queue |
| `on-document-parsed` | `DocumentParsed` | Tagging Lambda |
| `on-document-tagged` | `DocumentTagged` | Extract Lambda |
| `on-sections-ready` | `SectionsReady` | Agent Lambda (×5 concurrent) |
| `on-all-agents-complete` | `AllAgentsComplete` | Compile Lambda |
| `on-document-compiled-persist` | `DocumentCompiled` | Persist Lambda |
| `on-document-compiled-move` | `DocumentCompiled` | S3 Move Lambda |
| `on-finalise-ready` | `FinaliseReady` | Notify Lambda |

---

## AWS Services Summary

| Stage | Services |
|-------|----------|
| 2 — Upload detection | S3 + EventBridge (native integration) + SQS FIFO + DLQ |
| 3 — Parse | Lambda + ElastiCache (Redis) + Textract (scanned PDF fallback) |
| 4 — Tagging | Lambda + Claude API + ElastiCache (Redis) |
| 5 — Extract sections | Lambda + ElastiCache (Redis) + EventBridge fan-out |
| 6 — Specialist agents | Lambda (parallel) + RDS PostgreSQL + RDS Proxy + ElastiCache (Redis) |
| 7 — Compile | Lambda + ElastiCache (Redis) |
| 8 — Persist + Move | Lambda (×2 concurrent) + RDS PostgreSQL + S3 |
| 9 — Notify + pop queue | Lambda + SNS + SQS delete_message |
| Observability | CloudWatch Metrics + Alarms + Logs + Dashboard |
| State / caching | ElastiCache (Redis) — shared across all stages |
