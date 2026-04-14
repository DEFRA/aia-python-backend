# Plan 06 — Specialist Agents + Agent Lambda (Stage 6)

**Priority:** 6

**Depends on:** Plan 01, Plan 05 (SQS Tasks messages with document text and questions)

---

## Goal

1. Build the four remaining specialist agents (`data`, `risk`, `ea`, `solution`)
   following the pattern established by `security_agent.py`
2. Create their prompt files in `src/agents/prompts/`
3. Implement `src/handlers/agent.py` — the SQS-triggered Stage 6 Lambda handler

The security agent already exists and should not be modified except to ensure it
conforms to the interface described below.

---

## Agent interface

All agents must expose:

```python
class <Name>Agent:
    def __init__(self, client: anthropic.AsyncAnthropic, config: <Name>AgentConfig) -> None: ...
    async def assess(self, document: str, questions: list[dict]) -> AgentResult: ...
```

`AgentResult` is already defined in `src/agents/schemas.py`.

The `document` parameter is the full extracted text of the document (plain string).
The `questions` parameter is a list of `{"id": int, "question": str}` dicts — the
checklist questions for this agent type, included directly in the SQS task message.

---

## Task message (input)

Each agent Lambda is invoked by SQS Tasks via event source mapping. The message
body contains the document text (inline or via S3 pointer) and the agent-specific
questions.

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

**S3 pointer** — used when the payload exceeds 256 KB:

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

When `s3PayloadKey` is present, the Lambda fetches the full JSON payload from S3
and reads the `document` field from it.

---

## Handler flow

A single handler module (`src/handlers/agent.py`) is shared across all 5 Lambda
functions. Each function is configured with an `AGENT_TYPE` environment variable,
but the handler dispatches by the `agentType` field in the SQS message body.

```python
AGENT_REGISTRY: dict[str, type] = {
    "security": SecurityAgent,
    "data":     DataAgent,
    "risk":     RiskAgent,
    "ea":       EAAgent,
    "solution": SolutionAgent,
}

CONFIG_REGISTRY: dict[str, type] = {
    "security": SecurityAgentConfig,
    "data":     DataAgentConfig,
    "risk":     RiskAgentConfig,
    "ea":       EAAgentConfig,
    "solution": SolutionAgentConfig,
}

async def _handler(event: dict, context: object) -> dict:
    for record in event["Records"]:
        start_time = time.monotonic()
        body = json.loads(record["body"])
        doc_id = body["docId"]
        agent_type = body["agentType"]

        # Resolve document: inline or S3 pointer
        if "s3PayloadKey" in body:
            document = _fetch_from_s3(body["s3PayloadKey"])
        else:
            document = body["document"]
        questions = body["questions"]

        try:
            agent_cls = AGENT_REGISTRY[agent_type]
            config = CONFIG_REGISTRY[agent_type]()
            agent = agent_cls(client=anthropic.AsyncAnthropic(), config=config)
            result: AgentResult = await agent.assess(document=document, questions=questions)
            elapsed_ms = (time.monotonic() - start_time) * 1000

            sqs.send_message(QueueUrl=STATUS_QUEUE_URL, MessageBody=json.dumps({
                "docId": doc_id,
                "agentType": agent_type,
                "status": "completed",
                "result": result.model_dump(),
                "durationMs": elapsed_ms,
                "completedAt": datetime.now(tz=timezone.utc).isoformat(),
            }))

            _emit_metric("AgentDuration", elapsed_ms, agent_type)
            _emit_metric("AgentSuccess", 1, agent_type)

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            sqs.send_message(QueueUrl=STATUS_QUEUE_URL, MessageBody=json.dumps({
                "docId": doc_id,
                "agentType": agent_type,
                "status": "failed",
                "result": None,
                "durationMs": elapsed_ms,
                "completedAt": datetime.now(tz=timezone.utc).isoformat(),
                "errorMessage": str(e),
            }))
            _emit_metric("AgentFailure", 1, agent_type)

    return {"statusCode": 200}
```

---

## S3 pointer resolution

When the task message contains `s3PayloadKey` instead of `document`, the handler
fetches the full JSON payload from S3 and extracts the `document` field:

```python
def _fetch_from_s3(s3_key: str) -> str:
    """Fetch document text from S3 when payload exceeds SQS inline limit."""
    response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
    payload = json.loads(response["Body"].read())
    return payload["document"]
```

---

## Status message (output)

Results are published to SQS Status. Both success and failure formats are shown.

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

SQS message deletion is handled automatically by Lambda on successful return.
On unhandled exception Lambda returns the message to the queue for retry — after
3 failed receives the message is routed to the per-agent DLQ.

---

## CloudWatch metrics

Three custom metrics emitted before SQS Status publish (so metrics are recorded
even if the Status queue publish fails):

```python
def _emit_metric(metric_name: str, value: float, agent_type: str) -> None:
    """Emit a custom CloudWatch metric with agentType dimension."""
    cloudwatch.put_metric_data(
        Namespace="DefraPipeline",
        MetricData=[{
            "MetricName": metric_name,
            "Dimensions": [{"Name": "agentType", "Value": agent_type}],
            "Value": value,
            "Unit": "Milliseconds" if metric_name == "AgentDuration" else "Count",
        }],
    )
```

| Metric | Unit | Description |
|--------|------|-------------|
| `AgentDuration` | Milliseconds | Wall-clock time for the agent call |
| `AgentSuccess` | Count | Incremented on successful completion |
| `AgentFailure` | Count | Incremented on failure |

All metrics have `agentType` as a dimension.

---

## Lambda configuration

| Setting | Value |
|---------|-------|
| Functions | `agent-security`, `agent-data`, `agent-risk`, `agent-ea`, `agent-solution` |
| Runtime | Python 3.12 |
| Timeout | 15 min |
| Memory | 512 MB |
| Event source | SQS Tasks (Standard) — batch size 1 |
| Concurrency | Reserved concurrency per function |
| DLQ | Per-agent DLQ after 3 failed receives |

All 5 functions share the same handler code (`src/handlers/agent.py`). Each is
configured with an `AGENT_TYPE` environment variable and a `STATUS_QUEUE_URL`
environment variable pointing to the SQS Status queue.

---

## Prompt files

Each prompt file in `src/agents/prompts/` exports two constants:

```python
SYSTEM_PROMPT: str   # Few-shot examples with Green/Amber/Red ratings
USER_PROMPT_TEMPLATE: str  # f-string template with {document} and {questions} slots
```

### `src/agents/prompts/data.py` — Data governance agent

Focus areas: data classification, retention policies, data ownership, lineage,
GDPR/compliance evidence, audit logging of data access.

Assessment criteria:
- **Green**: Data classification scheme defined, retention policy documented, DPA registered, audit logs cover data access, personal data inventory maintained
- **Amber**: Some controls present but gaps in classification or retention enforcement
- **Red**: No data classification, no retention policy, no GDPR controls, no audit logging

### `src/agents/prompts/risk.py` — Risk management agent

Focus areas: risk register, incident response plan, breach notification procedures,
SLA for remediation, escalation paths, business continuity.

Assessment criteria:
- **Green**: Formal risk register, tested IR plan, breach notification within 72h (GDPR), defined SLAs, documented escalation
- **Amber**: IR plan exists but untested, or risk register not regularly reviewed
- **Red**: No IR plan, no risk register, no breach notification procedure

### `src/agents/prompts/ea.py` — Enterprise architecture agent

Focus areas: network segmentation, encryption in transit and at rest, infrastructure
security patterns, cloud architecture security, TLS standards, key management.

Assessment criteria:
- **Green**: TLS 1.2+ enforced, AES-256 at rest, KMS key rotation, network segmentation documented, least-privilege IAM
- **Amber**: Encryption present but gaps (e.g. TLS 1.0 still permitted, no key rotation)
- **Red**: Unencrypted data at rest or in transit, no network segmentation

### `src/agents/prompts/solution.py` — Solution design / cross-cutting agent

Focus areas: all security domains — produces the executive summary and overall
Green/Amber/Red rating that synthesises the other four agents.

Receives the full document text. Should reference the scoring guide in
`files/scoring_guide.txt` when producing its overall assessment.

---

## Config classes

Add to `src/config.py` — one per agent following the `SecurityAgentConfig` pattern:

```python
class DataAgentConfig(BaseSettings): ...
class RiskAgentConfig(BaseSettings): ...
class EAAgentConfig(BaseSettings): ...
class SolutionAgentConfig(BaseSettings): ...
```

At minimum, each must expose `model: str` (defaulting to `"claude-sonnet-4-6"`).

---

## Verification

```bash
python -c "from src.agents.data_agent import DataAgent"
python -c "from src.agents.risk_agent import RiskAgent"
python -c "from src.agents.ea_agent import EAAgent"
python -c "from src.agents.solution_agent import SolutionAgent"
python -c "from src.handlers.agent import lambda_handler"
python -m pytest tests/test_agent_handler.py tests/test_specialist_agents.py -v
ruff check src/agents/ src/handlers/agent.py
mypy src/agents/ src/handlers/agent.py
```

---

## Acceptance Criteria

- [ ] Four new agent classes (`DataAgent`, `RiskAgent`, `EAAgent`, `SolutionAgent`) with `__init__(client, config)` + `assess(document: str, questions: list[dict]) -> AgentResult`
- [ ] Agent receives document text inline or fetches from S3 via `s3PayloadKey`
- [ ] Results published to SQS Status with `status`, `result`, `durationMs`, `completedAt`
- [ ] Failure path publishes failed status to SQS Status — not raised to Lambda (allows controlled retry)
- [ ] `AgentDuration`, `AgentSuccess`, `AgentFailure` CloudWatch metrics with `agentType` dimension
- [ ] SQS message deletion is automatic on successful Lambda return
- [ ] Four new prompt files with `SYSTEM_PROMPT` including few-shot Green/Amber/Red examples
- [ ] Four new config classes in `src/config.py`
- [ ] All agents use `temperature=0.0`
- [ ] Per-agent DLQ after 3 failed receives
- [ ] Unit tests: mock Anthropic client, assert `AgentResult` structure for each agent
- [ ] `ruff check .` and `mypy src/` pass
