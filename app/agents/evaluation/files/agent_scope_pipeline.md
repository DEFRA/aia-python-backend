# Agent Scope Pipeline — SQS Tasks → Lambda Agents → SQS Status

Scope: from receiving a task message off **SQS Tasks** to publishing results to **SQS Status**. Upstream pipeline construction and downstream result processing are out of scope.

---

## Boundary Definition

```
[SQS Tasks]  ──►  Lambda Agents  ──►  [SQS Status]
      ▲                                     │
   out of scope                        out of scope
   (task build)                      (aggregation / compile)
```

| Boundary | Detail |
|----------|--------|
| **Entry** | Lambda agent is invoked by SQS Tasks containing the full extracted document text and agent-specific questions |
| **Exit** | Lambda agent publishes its full result (or failure) to SQS Status |
| **Observability** | CloudWatch captures agent duration, errors, and queue depth throughout |

---

## Why Lambda, Not ECS Fargate

Each agent receives a message, calls Claude, and publishes a result — a short, stateless, request-response operation. Lambda is the natural fit.

| | Lambda | ECS Fargate |
|--|--------|-------------|
| SQS integration | Native event source mapping — no polling loop needed | Must implement polling loop manually |
| Scaling | Automatic, per-message | Manual auto scaling policy via queue depth metric |
| Concurrency | One invocation per SQS message, out of the box | Requires `asyncio.gather` or task management |
| Infrastructure | None to manage | Cluster, task definitions, service config |
| Cost | Pay per invocation | Pay per running task (including idle time) |
| 15-min limit | Only a concern if a single Claude call exceeds 15 min | No limit |

ECS Fargate would only be justified if a Claude call on a very large document were expected to exceed Lambda's 15-minute limit. For typical assessment workloads this is not the case, so the additional infrastructure overhead is unnecessary.

---

## Core Pattern: SQS Event Source Mapping → Async Claude Call → SQS Status Publish

SQS Tasks is configured as a **Lambda event source mapping**. AWS invokes one Lambda function per message automatically — no polling loop, no concurrency management. When Claude responds the full result is published to SQS Status. The Lambda writes no state anywhere else.

---

## Document Format — Why Extracted Text, Not Raw Files

Two constraints determine how the document is carried in the task message.

**SQS message size limit — 256 KB**

Raw PDF and DOCX files almost always exceed this. Neither format can be placed inline in an SQS message. The document content must be extracted to plain text upstream (before the task is enqueued) so it fits within the message. If the extracted text still exceeds 256 KB, the full payload is stored in S3 and the SQS message carries only the S3 key. The Lambda fetches the content from S3 at invocation time.

**Claude API native format support**

| Format | Claude native support | Notes |
|--------|-----------------------|-------|
| **PDF** | ✅ Yes | Accepted as a base64-encoded `document` content block — Claude reads layout, structure, and text directly |
| **DOCX** | ❌ No | No native support — text must be extracted first (e.g. via `python-docx`) |

Since DOCX always requires extraction and raw files cannot be inlined into SQS regardless, text extraction is applied to both formats upstream. The agent receives a consistent plain-text `document` field in every task message — no file handling or format detection is needed inside the agent scope.

---

## Task Message (Input)

Five agent types consume from the same queue: `security`, `data`, `risk`, `ea`, `solution`.

**Inline** — used when extracted document text fits within 256 KB:

```json
{
  "docId": "UUID-1234",
  "agentType": "security",
  "document": "Full document text content...",
  "questions": [
    { "id": 1, "question": "Does the document define access control policies?" },
    { "id": 2, "question": "Is encryption at rest addressed?" }
  ],
  "enqueuedAt": "2026-03-27T10:01:00Z"
}
```

**S3 pointer** — used when the payload exceeds 256 KB. The Lambda resolves the S3 key and fetches the full content before calling Claude:

```json
{
  "docId": "UUID-1234",
  "agentType": "security",
  "s3PayloadKey": "payloads/UUID-1234/security.json",
  "questions": [
    { "id": 1, "question": "Does the document define access control policies?" },
    { "id": 2, "question": "Is encryption at rest addressed?" }
  ],
  "enqueuedAt": "2026-03-27T10:01:00Z"
}
```

---

## Agent Handler

```python
async def handler(event, context):
    for record in event["Records"]:
        start_time = now()
        body      = json.loads(record["body"])
        docId     = body["docId"]
        agentType = body["agentType"]
        document  = body["document"]
        questions = body["questions"]

        try:
            # Async Claude call
            result = await claude.assess(document, questions)

            # Publish full result to SQS Status
            sqs.send_message(QueueUrl=STATUS_QUEUE, MessageBody={
                "docId":       docId,
                "agentType":   agentType,
                "status":      "completed",
                "result":      result.dict(),
                "durationMs":  elapsed_ms(start_time),
                "completedAt": now()
            })

        except Exception as e:
            # Publish failure to SQS Status — downstream aggregator handles retry logic
            sqs.send_message(QueueUrl=STATUS_QUEUE, MessageBody={
                "docId":        docId,
                "agentType":    agentType,
                "status":       "failed",
                "result":       None,
                "durationMs":   elapsed_ms(start_time),
                "completedAt":  now(),
                "errorMessage": str(e)
            })
```

SQS message deletion is handled automatically by Lambda on successful return. On unhandled exception Lambda returns the message to the queue for retry up to the configured maximum receives before routing to the DLQ.

---

## Status Message (Output)

**Success:**
```json
{
  "docId": "UUID-1234",
  "agentType": "security",
  "status": "completed",
  "result": {
    "rows": [
      {
        "question": "Does the document define access control policies?",
        "rating": "Green",
        "evidence": "Section 3.2 defines role-based access control...",
        "recommendation": "No action required."
      }
    ],
    "summary": "The document adequately addresses access control and encryption.",
    "overallRating": "Amber"
  },
  "durationMs": 4200,
  "completedAt": "2026-03-27T10:03:00Z",
  "errorMessage": null
}
```

**Failure:**
```json
{
  "docId": "UUID-1234",
  "agentType": "security",
  "status": "failed",
  "result": null,
  "durationMs": 1100,
  "completedAt": "2026-03-27T10:03:00Z",
  "errorMessage": "Claude API timeout after 3 retries"
}
```

---

## Lambda Configuration

| Setting | Value |
|---------|-------|
| Function per agent type | `agent-security`, `agent-data`, `agent-risk`, `agent-ea`, `agent-solution` |
| Runtime | Python 3.12 |
| Timeout | 15 min — covers the longest expected Claude call |
| Memory | 512 MB |
| Event source | SQS Tasks (Standard) — batch size 1 |
| Concurrency | Reserved concurrency per function — prevents one agent type monopolising the queue |
| DLQ | After 3 failed receives, message routed to per-agent DLQ |

---

## CloudWatch Observability

### Metrics

| Metric | Source | Dimension | Purpose |
|--------|--------|-----------|---------|
| `AgentDuration` | Lambda (emitted before SQS Status publish) | `agentType` | Identifies slow agent types |
| `AgentSuccess` | Lambda | `agentType` | Count of completed results |
| `AgentFailure` | Lambda | `agentType` | Count of failed results |
| `TasksQueueDepth` | SQS built-in (`ApproximateNumberOfMessagesVisible`) | — | Monitors backlog |
| `TasksOldestMessage` | SQS built-in (`ApproximateAgeOfOldestMessage`) | — | Detects stalled invocations |

Agents emit `AgentDuration`, `AgentSuccess`, and `AgentFailure` directly via `cloudwatch.put_metric_data()` before publishing to SQS Status, so metrics are recorded even if the Status queue publish fails.

### Alarms

| Alarm | Condition | Action |
|-------|-----------|--------|
| `AgentFailureRate` | `AgentFailure > 2` in any 5-min window | SNS alert → on-call |
| `TasksOldestMessage` | Message age > 10 min | SNS alert — agent may be hung |
| `TasksQueueDepth` | Depth > 50 | SNS alert — invocations not keeping up |
| `DLQDepth` | Any message on a per-agent DLQ | SNS alert — unrecoverable failure |

### Log Groups

| Log Group | Content |
|-----------|---------|
| `/defra/agents/{agentType}` | Structured JSON log per invocation: `docId`, `agentType`, `status`, `durationMs`, `errorMessage` |
| `/defra/agents/errors` | Failures only — aggregated across all agent types for easier alerting |

Every log entry includes `docId` and `agentType` as top-level fields to enable cross-agent correlation via CloudWatch Logs Insights:

```sql
fields docId, agentType, status, durationMs
| filter status = "failed"
| sort durationMs desc
```

### Dashboard

A single **CloudWatch Dashboard** (`defra-agents`) surfaces:

- Queue depth over time (SQS Tasks)
- Agent duration by type (p50 / p95)
- Success vs failure rate by agent type
- Lambda concurrent executions over time
- DLQ depth per agent type
- Active alarms panel
