# Plan 05 — Extract Sections Lambda (Stage 5)

**Priority:** 5

**Depends on:** Plan 01, Plan 02, Plan 04 (DocumentTagged event + tagged chunks in Redis)

---

## Goal

Implement `src/handlers/extract_sections.py` — the Stage 5 Lambda that:

1. Reads the full tagged chunk array from Redis
2. Filters it into 5 agent-specific payloads using `extract_sections_for_agent()`
3. Serialises each payload to plain text via `_sections_to_text()`
4. Fetches agent-specific questions from DB (cached in Redis)
5. Enqueues 5 × SQS Tasks messages (fan-out) — inline or via S3 pointer

This is the **fan-out point** of the pipeline. After this stage, all five specialist
agents run concurrently via SQS event source mapping.

---

## Tag-to-agent routing

Each agent type is interested in a specific subset of the TAXONOMY tags:

| Agent | Relevant tags |
|-------|--------------|
| `security` | `authentication`, `authorisation`, `encryption`, `vulnerability_management`, `secrets_management`, `network_security` |
| `data` | `data_governance`, `compliance`, `audit_logging` |
| `risk` | `incident_response`, `compliance`, `audit_logging` |
| `ea` | `encryption`, `network_security`, `compliance` |
| `solution` | `authentication`, `authorisation`, `vulnerability_management`, `secrets_management`, `encryption`, `network_security`, `incident_response`, `data_governance`, `audit_logging`, `compliance` |

`solution` receives all relevant chunks — it produces the cross-cutting summary.

These mappings should be defined as a constant in `src/handlers/extract_sections.py`:

```python
AGENT_TAG_MAP: dict[str, frozenset[str]] = {
    "security": frozenset({
        "authentication", "authorisation", "encryption",
        "vulnerability_management", "secrets_management", "network_security",
    }),
    "data": frozenset({"data_governance", "compliance", "audit_logging"}),
    "risk": frozenset({"incident_response", "compliance", "audit_logging"}),
    "ea":   frozenset({"encryption", "network_security", "compliance"}),
    "solution": frozenset({
        "authentication", "authorisation", "encryption",
        "vulnerability_management", "secrets_management", "network_security",
        "incident_response", "data_governance", "audit_logging", "compliance",
    }),
}

AGENT_TYPES: list[str] = ["security", "data", "risk", "ea", "solution"]
```

---

## `extract_sections_for_agent()`

```python
def extract_sections_for_agent(
    tagged_chunks: list[dict],
    agent_type: str,
) -> list[dict]:
    """Filter tagged chunks for a specific agent type.

    Includes:
    - Chunks where `relevant=True` and at least one tag matches the agent's set
    - The nearest preceding heading chunk (is_heading=True), even if not itself
      tagged for this agent — provides section context

    Args:
        tagged_chunks: Full list of TaggedChunk dicts from Stage 4.
        agent_type: One of "security", "data", "risk", "ea", "solution".

    Returns:
        Filtered list preserving original chunk order.
    """
    allowed_tags = AGENT_TAG_MAP[agent_type]
    result: list[dict] = []
    last_heading: dict | None = None

    for chunk in tagged_chunks:
        if chunk.get("is_heading"):
            last_heading = chunk

        tags = set(chunk.get("tags", []))
        is_relevant = chunk.get("relevant", False) and bool(tags & allowed_tags)

        if is_relevant:
            # Prepend nearest heading for section context (if not already included)
            if last_heading and (not result or result[-1] != last_heading):
                result.append(last_heading)
            result.append(chunk)

    return result
```

---

## `src/handlers/extract_sections.py` — Handler flow

```python
async def _handler(event: dict, context: object) -> dict:
    # 1. Parse trigger event
    tagged_detail = DocumentTaggedDetail.model_validate(event["detail"])
    doc_id = tagged_detail.docId
    tagged_key = tagged_detail.taggedCacheKey

    redis = await get_redis()

    # 2. Load tagged chunks from Redis
    tagged_chunks = await redis_get_json(redis, tagged_key)
    if tagged_chunks is None:
        raise RuntimeError(f"Tagged cache miss: {tagged_key}")

    # 3. For each agent: extract sections, fetch questions, enqueue to SQS Tasks
    for agent_type in AGENT_TYPES:
        sections = extract_sections_for_agent(tagged_chunks, agent_type)
        questions = await _load_questions(redis, agent_type)  # DB + Redis cache

        # Serialise sections to plain text for SQS message
        document = _sections_to_text(sections)

        payload = {
            "docId": doc_id,
            "agentType": agent_type,
            "questions": questions,
            "enqueuedAt": datetime.now(tz=timezone.utc).isoformat(),
        }

        # Check if payload fits inline (SQS 256 KB limit)
        inline_payload = {**payload, "document": document}
        if len(json.dumps(inline_payload).encode()) <= 240_000:
            sqs.send_message(QueueUrl=TASKS_QUEUE_URL, MessageBody=json.dumps(inline_payload))
        else:
            # Write full payload to S3, send pointer in SQS
            s3_key = f"payloads/{doc_id}/{agent_type}.json"
            s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps({**payload, "document": document}))
            sqs.send_message(QueueUrl=TASKS_QUEUE_URL, MessageBody=json.dumps({**payload, "s3PayloadKey": s3_key}))

    return {"statusCode": 200}
```

### `_load_questions` — fetch from DB, cache in Redis

```python
async def _load_questions(redis: aioredis.Redis, agent_type: str) -> list[dict]:
    """Load checklist questions from DB and cache in Redis."""
    cached = await redis_get_json(redis, key_questions(agent_type))
    if cached is not None:
        return cached
    rows = await fetch_questions_by_category(agent_type)
    questions = [{"id": r["id"], "question": r["question"]} for r in rows]
    await redis_set_json(redis, key_questions(agent_type), questions, TTL_QUESTIONS)
    return questions
```

### Why extract all sections before enqueueing any tasks

All five section extractions and question lookups must complete before the first
SQS Tasks message is enqueued. This ensures that if a section extraction or
question fetch fails, no partial fan-out occurs — either all 5 tasks are enqueued
or none are. Extract all, then enqueue all.

---

## CloudWatch metric

Emit `SectionCount` per agent type dimension after the fan-out:

```python
# e.g. security: 8 chunks, data: 3 chunks, etc.
for agent_type, count in section_counts.items():
    cloudwatch.put_metric_data(...)
```

---

## Verification

```bash
python -c "from src.handlers.extract_sections import lambda_handler, extract_sections_for_agent"
python -m pytest tests/test_extract_sections.py -v
ruff check src/handlers/extract_sections.py
mypy src/handlers/extract_sections.py
```

---

## Acceptance Criteria

- [ ] `AGENT_TAG_MAP` and `AGENT_TYPES` defined as module-level constants
- [ ] `extract_sections_for_agent()` includes nearest preceding heading for context
- [ ] Sections serialised to plain text via `_sections_to_text()`
- [ ] Payload checked against 240 KB threshold; large payloads written to S3 with `s3PayloadKey` in SQS message
- [ ] Questions fetched from DB and cached in Redis per `questions:{agentType}` before enqueueing
- [ ] Exactly 5 SQS Tasks messages enqueued per invocation
- [ ] `SectionCount` CloudWatch metric emitted per agent type
- [ ] Unit tests: verify tag filtering, heading injection, and correct chunk ordering
- [ ] Unit tests: mock SQS, assert 5 messages with correct `agentType` values
- [ ] `ruff check .` and `mypy src/` pass
