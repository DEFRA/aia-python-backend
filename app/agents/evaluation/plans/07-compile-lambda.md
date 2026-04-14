# Plan 07 — Compile Lambda (Stage 7)

**Priority:** 7

**Depends on:** Plan 01, Plan 02, Plan 06 (SQS Status messages from Stage 6 with agent results)

---

## Goal

Implement `src/handlers/compile.py` — the Stage 7 Lambda that:

1. Consumes agent status messages from SQS Status (event source mapping, batch size 1)
2. Writes each agent result to Redis and increments the completion counter
3. When all 5 agents have completed, reads all results and assembles the `CompiledResult`
4. Publishes `DocumentCompiled` to EventBridge

---

## `src/agents/schemas.py` — Add `AgentStatusMessage` and `CompiledResult`

```python
class AgentStatusMessage(BaseModel):
    docId: str
    agentType: str
    status: Literal["completed", "failed"]
    result: dict | None
    durationMs: float
    completedAt: str
    errorMessage: str | None = None


class CompiledResult(BaseModel):
    docId: str
    type: str                   # e.g. "Solution Design Team"
    generatedAt: datetime
    content: list[dict]         # [{"type": "text", "text": "...markdown..."}]
    status: Literal["completed", "error"]
    processedAt: datetime
```

`AgentStatusMessage` validates the SQS Status message body at the handler boundary.
`CompiledResult` matches the `front_end_response` shape in `files/front_end_response.json`
and the schema documented in `aws_event_driven_orchestration.md` Stage 7.

---

## `src/handlers/compile.py` — Handler flow

The compile Lambda is event-source-mapped to SQS Status. Each invocation processes
one status message, writes the result to Redis, increments the counter, and compiles
when all 5 agents have reported.

```python
AGENT_TYPES = ["security", "data", "risk", "ea", "solution"]

async def _handler(event: dict, context: object) -> dict:
    # 1. Parse SQS Status message
    for record in event["Records"]:
        body = json.loads(record["body"])
        status_msg = AgentStatusMessage.model_validate(body)
        doc_id = status_msg.docId
        agent_type = status_msg.agentType

        redis = await get_redis()
        eb = EventBridgePublisher()

        # 2. Write result to Redis (even for failed agents — store None)
        if status_msg.result is not None:
            await redis_set_json(
                redis, key_result(doc_id, agent_type), status_msg.result, TTL_RESULT
            )
        else:
            await redis_set_json(
                redis, key_result(doc_id, agent_type), {"status": "failed", "error": status_msg.errorMessage}, TTL_RESULT
            )

        # 3. Increment completion counter
        count = await redis_incr(redis, key_results_count(doc_id), TTL_RESULTS_COUNT)

        # 4. When all 5 agents complete, compile
        if count == len(AGENT_TYPES):
            results = {}
            for at in AGENT_TYPES:
                raw = await redis_get_json(redis, key_result(doc_id, at))
                if raw is not None:
                    results[at] = AgentResult.model_validate(raw) if "rows" in raw else None
            compiled = _assemble(doc_id, results)
            compiled_key = key_compiled(doc_id)
            await redis_set_json(redis, compiled_key, compiled.model_dump(mode="json"), TTL_COMPILED)
            await eb.publish(
                detail_type="DocumentCompiled",
                detail=DocumentCompiledDetail(docId=doc_id, compiledCacheKey=compiled_key).model_dump(),
            )

    return {"statusCode": 200}
```

### `_assemble()` — building the markdown content

```python
def _assemble(doc_id: str, results: dict[str, AgentResult]) -> CompiledResult:
    """Merge five AgentResult objects into the front_end_response shape."""
    now = datetime.now(tz=timezone.utc)

    # Build one markdown table per agent, concatenated into a single text block
    sections: list[str] = []
    for agent_type in AGENT_TYPES:
        result = results[agent_type]
        sections.append(_render_agent_markdown(agent_type, result))

    markdown_body = "\n\n".join(sections)

    return CompiledResult(
        docId=doc_id,
        type=_infer_doc_type(results),
        generatedAt=now,
        content=[{"type": "text", "text": markdown_body}],
        status="completed",
        processedAt=now,
    )
```

### `_render_agent_markdown()` — per-agent markdown table

Each `AgentResult` contains a list of `AssessmentRow` objects (defined in the
existing `schemas.py`). Render them as a markdown table:

```markdown
## Security Assessment

| Area | Rating | Evidence | Recommendation |
|------|--------|----------|----------------|
| Access Control | Green | MFA enforced on all accounts | — |
| Encryption | Amber | TLS 1.2 in use; TLS 1.3 not yet enforced | Upgrade to TLS 1.3 |
| ... | ... | ... | ... |
```

Use the `FinalSummary` from `AgentResult` for the summary row at the bottom.

### `_infer_doc_type()` — document type heuristic

Infer document type from the solution agent's `FinalSummary.doc_type` if present,
otherwise default to `"Security Assessment"`.

---

## Verification

```bash
python -c "from src.handlers.compile import lambda_handler"
python -c "from src.agents.schemas import CompiledResult, AgentStatusMessage"
python -m pytest tests/test_compile.py -v
ruff check src/handlers/compile.py
mypy src/handlers/compile.py
```

---

## Acceptance Criteria

- [ ] `AgentStatusMessage` Pydantic model in `src/agents/schemas.py`
- [ ] `CompiledResult` Pydantic model in `src/agents/schemas.py`
- [ ] Compile Lambda event-source-mapped to SQS Status (batch size 1)
- [ ] Each invocation writes agent result to Redis and increments counter
- [ ] When counter == 5, reads all 5 results and assembles `CompiledResult`
- [ ] Failed agent results stored in Redis with `{"status": "failed", "error": ...}`
- [ ] `DocumentCompiled` event published to EventBridge only when all 5 agents complete
- [ ] `_assemble()` builds a `CompiledResult` with markdown content from all 5 agents
- [ ] Compiled result written to Redis under `compiled:{docId}` with 1h TTL
- [ ] Unit tests: provide mock `AgentResult` objects, assert `CompiledResult` structure and markdown content
- [ ] `ruff check .` and `mypy src/` pass
