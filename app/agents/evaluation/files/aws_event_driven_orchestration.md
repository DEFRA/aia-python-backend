# AWS Event-Driven Orchestration — Document Processing Pipeline

End-to-end architecture using SQS, EventBridge, S3, and CloudWatch. **No Redis** — every cross-stage handoff travels in the EventBridge event itself, with an inline-or-S3 envelope for payloads larger than the SQS / EventBridge limit.

The evaluation pipeline consists of **four AWS Lambda functions** — Parse, Tag, Extract Sections, and Agent. Code lives in [`src/handlers/`](../src/handlers/). The web upload (FastAPI) and the S3 → EventBridge → SQS Tasks routing in front of Parse are not Lambdas; they are described here for context.

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
    "payload": { "inline": "[{...chunks...}]" }
  }
}
```

The `payload` field is a discriminated union: either `{"inline": "<json string>"}` (under 240 KB) or `{"s3Key": "state/{docId}/{stage}.json"}` (over 240 KB).  See [Inline-or-S3 payload offload](#inline-or-s3-payload-offload).

---

## Full Event Flow

```
S3 upload (or Ingestor service)
  → EventBridge (Object Created)
    → SQS Tasks queue   message body: {docId, s3Key}
      → Parse Lambda    publishes: DocumentParsed   (payload: chunks)
        → Tag Lambda    publishes: DocumentTagged   (payload: tagged chunks)
          → Extract Sections Lambda
                        fans out 2 SQS Tasks messages (one per agent type) directly to the Tasks queue
            → Agent Lambda ×2 (parallel SQS invocations)
                        each publishes: AgentStatusMessage to SQS Status queue
```

Terminal output: one `AgentStatusMessage` per `(docId, agentType)` on the SQS Status queue. Consumption of that queue is the responsibility of an external front-end / downstream service and is **out of scope** for this codebase.

Only **two events** travel on the EventBridge bus: `DocumentParsed` (Stage 3 → 4) and `DocumentTagged` (Stage 4 → 5). Stage 5 to Stage 6 hand-off is via SQS (the same Tasks queue), not EventBridge. Stage 6 publishes only to SQS Status. There is no `SectionsReady` or `AgentComplete` event in the live path (the corresponding Pydantic detail models exist in [`schemas.py`](../src/agents/schemas.py) for future observability hooks but no handler currently emits them).

- **EventBridge** carries the two intra-pipeline event transitions (`DocumentParsed`, `DocumentTagged`)
- **CloudWatch** observes every transition (per-stage duration metrics + SQS / Lambda standard metrics)
- **SQS** anchors durability and ordering at the entry point, the Stage 5 → 6 fan-out, and the terminal output
- **No Redis** — there is no shared cache or state store between stages

---

## Stage-by-Stage Breakdown

### Stage 2 — Upload Detection

```
S3 in_progress/  →  S3 EventBridge integration (native, no Lambda needed)
                 →  EventBridge rule: detail-type = "Object Created"
                 →  SQS Tasks queue (FIFO, MessageGroupId = "pipeline")
                 →  Parse Lambda (event-source mapping)
```

- S3 natively sends events to EventBridge — no SNS/notification config required.
- SQS FIFO preserves **earliest-timestamp-first** ordering.
- The Lambda event-source mapping deletes the SQS message **automatically on successful invocation**. Failures propagate as exceptions and trigger SQS redelivery up to `maxReceiveCount`, then route to a DLQ.
- A **Dead Letter Queue (DLQ)** catches messages that fail too many times.

**CloudWatch alarm:** `DLQ depth > 0` → page on-call.

---

### Stage 3 — Parse (PDF / DOCX)

`Parse Lambda` runs `extract_text_blocks()` + `clean_and_chunk()` for PDFs, or `python-docx` paragraph iteration for `.docx` files. Both paths produce the same chunk schema:

```json
{ "chunk_index", "page", "is_heading", "char_count", "text" }
```

For `.docx`, paragraph style name (`Heading 1`, `Heading 2`, `Normal`) replaces font-size heuristics for `is_heading`.

For scanned PDFs with no extractable text layer, the Lambda raises `ScannedPdfError`.

The chunk list is wrapped in an inline-or-S3 payload envelope and published:

```json
{
  "detail-type": "DocumentParsed",
  "detail": {
    "docId": "...",
    "payload": { "inline": "[{...chunks...}]" }
  }
}
```

If the chunks JSON exceeds 240 KB the helper writes them to `s3://{bucket}/state/{docId}/chunks.json` and emits `{"s3Key": "state/{docId}/chunks.json"}` instead.

**EventBridge rule:** `detail-type = "DocumentParsed"` → `Tag Lambda`.

---

### Stage 4 — Tag (LLM Taxonomy Pass)

`Tag Lambda` reads the `DocumentParsed` event, resolves the inline-or-S3 payload to recover the chunks, and runs the **TaggingAgent** which calls the LLM in batches (default 15 chunks per call). Every chunk is enriched with:

```json
{ "relevant": true|false, "tags": ["authentication", "encryption", ...], "reason": "..." }
```

The full tagged-chunk schema is `TaggedChunk` in [`schemas.py`](../src/agents/schemas.py).

The output is wrapped in another inline-or-S3 envelope and published:

```json
{
  "detail-type": "DocumentTagged",
  "detail": {
    "docId": "...",
    "payload": { "inline": "..." }   // or { "s3Key": "state/{docId}/tagged.json" }
  }
}
```

**EventBridge rule:** `detail-type = "DocumentTagged"` → `Extract Sections Lambda`.

---

### Stage 5 — Extract Sections (Fan-out)

`Extract Sections Lambda` reads the `DocumentTagged` event and:

1. Resolves the inline-or-S3 payload to recover tagged chunks.
2. For each agent type in `pipeline.agent_types` (currently `security`, `governance`):
   - Filters tagged chunks with `relevant=True` and at least one tag matching the agent's tag set (the `pipeline.agent_tag_map` in `config.yaml`).
   - Re-attaches the nearest preceding heading for context.
   - Loads checklist questions for that agent type via [`load_assessment_from_file`](../src/db/assessment_loader.py), which reads a JSON file from the data folder configured in `local_runner.assessment_filename`. The Postgres-backed equivalent ([`fetch_assessment_by_category`](../src/db/questions_repo.py)) is intentionally a `NotImplementedError` placeholder until the Postgres assessment schema lands.
   - Builds an `AgentTaskBody` carrying `(docId, agentType, document, questions, categoryUrl, enqueuedAt)`.
3. Sends one SQS Tasks message per agent. If any single message exceeds 240 KB the document text is offloaded to `s3://{bucket}/payloads/{docId}/{agentType}.json` and a pointer message with `s3PayloadKey` is sent instead.

This is the **fan-out** point: from this stage onwards, two independent agent invocations run in parallel. The hand-off is via SQS (back onto the same Tasks queue), not EventBridge.

---

### Stage 6 — Specialist Agents (Parallel) — Terminal Stage

`Agent Lambda` is one Lambda function that dispatches by `agentType`:

```python
AGENT_REGISTRY = {
    "security":   SecurityAgent,
    "governance": GovernanceAgent,
}
```

Triggered by SQS Tasks queue with batch size 1, so each invocation handles exactly one agent. The handler:

1. Validates the `AgentTaskBody`.
2. Resolves the document text inline or by downloading from S3 (`s3PayloadKey`).
3. Instantiates the registered agent and calls `await agent.assess(document, questions, category_url)`.
4. Publishes one **AgentStatusMessage** to the SQS Status queue.

```json
{
  "docId": "...",
  "agentType": "security",
  "status": "completed",
  "result": { "...AgentResult..." },
  "durationMs": 4127.3,
  "completedAt": "2026-04-28T10:00:00Z"
}
```

On exception, the same shape is published with `status: "failed"` and `errorMessage` populated. The SQS Status queue is the **terminal output** of this pipeline; consuming it is the responsibility of a separate front-end / downstream service.

---

## Inline-or-S3 payload offload

[`src/utils/payload_offload.py`](../src/utils/payload_offload.py) provides two functions:

```python
inline_or_s3(payload, doc_id, stage, s3_client, bucket, threshold=240_000) -> dict
resolve_payload(envelope, s3_client, bucket) -> bytes
```

- Below the threshold: returns `{"inline": "<json string>"}`. No S3 write.
- Above the threshold: writes `s3://{bucket}/state/{doc_id}/{stage}.json` and returns `{"s3Key": "..."}`.

The matching Pydantic discriminated union `PayloadEnvelope = InlinePayload | S3KeyPayload` lives in [`schemas.py`](../src/agents/schemas.py) so EventBridge detail models validate the envelope shape at the boundary.

---

## EventBridge Rules Reference

The custom event bus is `defra-pipeline`, carrying exactly two rules:

| Rule name | `detail-type` | Target | Detail Pydantic model |
|---|---|---|---|
| `pipeline-document-parsed` | `DocumentParsed` | `Tag Lambda` | `DocumentParsedDetail` |
| `pipeline-document-tagged` | `DocumentTagged` | `Extract Sections Lambda` | `DocumentTaggedDetail` |

Stage 5 fans out via SQS Tasks messages (not events), and Stage 6 publishes its result to SQS Status — the SQS Status queue is the terminal sink. Two further detail models exist in [`schemas.py`](../src/agents/schemas.py) (`SectionsReadyDetail`, `AgentCompleteDetail`) but **no handler currently publishes them**; they are reserved for future observability hooks.

---

## SQS Queues Reference

| Queue | Producer | Consumer | Notes |
|---|---|---|---|
| `aia-tasks` (FIFO) | Web upload + Stage 5 fan-out | Stage 3 / Stage 6 (Lambda event-source mapping) | Auto-deletion on success |
| `aia-status` | Stage 6 | External consumer (out of scope) | One message per `(docId, agentType)` |
| `aia-dlq` | All queues | Operators | Catches poison messages |

---

## Terminal Output

The pipeline ends at the SQS Status queue. This codebase publishes one `AgentStatusMessage` per `(docId, agentType)` and stops. Persisting results, building a compiled report, or moving the document to a "completed" S3 prefix are responsibilities of a separate front-end / downstream consumer and are out of scope for this repository.

---

## Why no Redis?

The pipeline is purely forward-flowing — every cache key would be written once and read at most once. Removing Redis:

- Eliminates an ElastiCache cluster.
- Cuts a network hop per stage.
- Simplifies handler code (no cache-aside, no fan-in counter).
- Removes a class of cache-coherency bugs.

Inter-stage state now travels in the EventBridge event itself, with S3 as the offload destination for payloads that exceed the 240 KB inline threshold. Determinism is preserved because re-runs simply re-parse and re-tag from scratch — both are tolerable to repeat once Redis is no longer in the path.

---

## Local development

The same handler code can be exercised end-to-end without any AWS infrastructure via [`main.py`](../main.py). The local runner mocks both ends of the SQS pipeline (Tasks input as a Python `SqsRecordBody`, Status output as a JSON file in the data folder) and skips EventBridge / CloudWatch entirely. Bedrock is the only AWS service it actually calls. See the [evaluation `README.md`](../README.md) for the full local-vs-production breakdown.
