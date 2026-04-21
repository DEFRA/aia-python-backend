# Plan 08 — Persist + S3 Move Lambdas (Stage 8)

**Priority:** 8

**Depends on:** Plan 01, Plan 02, Plan 07 (DocumentCompiled event + compiled result in Redis)

---

## Goal

Implement Stage 8 — two Lambdas triggered concurrently by the `DocumentCompiled` event:

- `src/handlers/persist.py` — writes the compiled result to PostgreSQL
- `src/handlers/s3_move.py` — moves the document from `in_progress/` to `completed/` or `error/`

Both publish events (`ResultPersisted`, `DocumentMoved`) that are counted by a Redis
counter. When count reaches 2, either Lambda publishes `FinaliseReady` to unblock Stage 9.

---

## `src/handlers/persist.py`

### Handler flow

```python
async def _handler(event: dict, context: object) -> dict:
    # 1. Parse trigger event
    detail = DocumentCompiledDetail.model_validate(event["detail"])
    doc_id = detail.docId
    compiled_key = detail.compiledCacheKey

    redis = await get_redis()
    eb = EventBridgePublisher()

    # 2. Load compiled result from Redis
    compiled_raw = await redis_get_json(redis, compiled_key)
    if compiled_raw is None:
        raise RuntimeError(f"Compiled cache miss: {compiled_key}")
    compiled = CompiledResult.model_validate(compiled_raw)

    # 3. Insert into PostgreSQL assessment_results
    await _upsert_result(compiled)

    # 4. Publish ResultPersisted
    await eb.publish(
        detail_type="ResultPersisted",
        detail=ResultPersistedDetail(docId=doc_id).model_dump(),
    )

    # 5. Increment Stage 8 counter → publish FinaliseReady if both done
    await _maybe_finalise(redis, eb, doc_id)

    return {"statusCode": 200}
```

### `_upsert_result()` — PostgreSQL write

```python
async def _upsert_result(compiled: CompiledResult) -> None:
    """Insert or update assessment_results row."""
    # Use asyncpg or the existing DB layer in src/db/
    # SQL:
    # INSERT INTO assessment_results
    #   (doc_id, doc_type, generated_at, processed_at, status, result_json)
    # VALUES ($1, $2, $3, $4, $5, $6)
    # ON CONFLICT (doc_id) DO UPDATE SET
    #   doc_type = EXCLUDED.doc_type,
    #   processed_at = EXCLUDED.processed_at,
    #   status = EXCLUDED.status,
    #   result_json = EXCLUDED.result_json;
```

Schema reference (from `aws_event_driven_orchestration.md`):

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

---

## `src/handlers/s3_move.py`

### Handler flow

```python
async def _handler(event: dict, context: object) -> dict:
    # 1. Parse trigger event
    detail = DocumentCompiledDetail.model_validate(event["detail"])
    doc_id = detail.docId
    compiled_key = detail.compiledCacheKey

    redis = await get_redis()
    eb = EventBridgePublisher()

    # 2. Load compiled result to determine success/error status
    compiled_raw = await redis_get_json(redis, compiled_key)
    if compiled_raw is None:
        raise RuntimeError(f"Compiled cache miss: {compiled_key}")
    compiled = CompiledResult.model_validate(compiled_raw)

    # 3. Determine destination prefix
    destination: Literal["completed", "error"] = (
        "completed" if compiled.status == "completed" else "error"
    )
    src_key  = f"in_progress/{doc_id}.pdf"   # or .docx — see note below
    dest_key = f"{destination}/{doc_id}.pdf"

    # 4. Copy then delete (S3 has no native move)
    s3 = boto3.client("s3")
    bucket = os.environ["S3_BUCKET"]
    s3.copy_object(CopySource={"Bucket": bucket, "Key": src_key}, Bucket=bucket, Key=dest_key)
    s3.delete_object(Bucket=bucket, Key=src_key)

    # 5. Publish DocumentMoved
    await eb.publish(
        detail_type="DocumentMoved",
        detail=DocumentMovedDetail(docId=doc_id, destination=destination).model_dump(),
    )

    # 6. Increment Stage 8 counter → publish FinaliseReady if both done
    await _maybe_finalise(redis, eb, doc_id)

    return {"statusCode": 200}
```

**Note on file extension:** The S3 key must preserve the original file extension
(PDF or DOCX). Either store the extension in Redis at Stage 3 (`ext:{docId}` key),
or include `s3Key` in the `DocumentCompiled` event detail and thread it through.
Recommended: add `s3Key: str` to `DocumentCompiledDetail` in Plan 07.

---

## Shared `_maybe_finalise()` helper

Both handlers call this after publishing their own event:

```python
async def _maybe_finalise(
    redis: aioredis.Redis,
    eb: EventBridgePublisher,
    doc_id: str,
) -> None:
    """Publish FinaliseReady when both Stage 8 branches are complete."""
    count = await redis_incr(redis, key_stage8_count(doc_id), TTL_STAGE8_COUNT)
    if count == 2:
        await eb.publish(
            detail_type="FinaliseReady",
            detail=FinaliseReadyDetail(docId=doc_id).model_dump(),
        )
```

This is idempotent — even if one Lambda retries and `INCR` produces 3, the
`FinaliseReady` event has already been published and Stage 9 will have completed.
(Stage 9 should be idempotent for the same reason.)

---

## `src/db/results_repo.py` — New DB helper

Add a new repository module alongside `questions_repo.py`:

```python
"""PostgreSQL repository for assessment results."""
from __future__ import annotations

from src.agents.schemas import CompiledResult


async def upsert_assessment_result(compiled: CompiledResult) -> None:
    """Insert or update a row in assessment_results."""
    ...
```

---

## Verification

```bash
python -c "from src.handlers.persist import lambda_handler"
python -c "from src.handlers.s3_move import lambda_handler"
python -c "from src.db.results_repo import upsert_assessment_result"
python -m pytest tests/test_persist.py tests/test_s3_move.py -v
ruff check src/handlers/persist.py src/handlers/s3_move.py src/db/results_repo.py
mypy src/handlers/persist.py src/handlers/s3_move.py src/db/results_repo.py
```

---

## Acceptance Criteria

- [ ] `src/handlers/persist.py` reads compiled result from Redis and upserts to PostgreSQL
- [ ] `src/handlers/s3_move.py` copies S3 object then deletes source; handles `.pdf` and `.docx`
- [ ] `src/db/results_repo.py` with `upsert_assessment_result()` using `ON CONFLICT DO UPDATE`
- [ ] Both handlers call `_maybe_finalise()` which publishes `FinaliseReady` when count == 2
- [ ] Stage 8 Redis counter TTL is 30 minutes
- [ ] Unit tests: mock S3, mock DB, assert both events published and counter behaviour
- [ ] `ruff check .` and `mypy src/` pass
