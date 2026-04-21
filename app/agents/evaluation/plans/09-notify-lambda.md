# Plan 09 — Notify Lambda (Stage 9)

**Priority:** 9

**Depends on:** Plan 01, Plan 02, Plan 08 (FinaliseReady event)

---

## Goal

Implement `src/handlers/notify.py` — the final stage Lambda that:

1. Publishes a notification to SNS (→ front-end webhook or push event)
2. Deletes the SQS message using the receipt handle stored in Redis (pops the queue)
3. Cleans up all Redis keys for the `docId`
4. Publishes `PipelineComplete` to EventBridge (audit + CloudWatch duration tracking)

---

## `src/handlers/notify.py` — Handler flow

```python
async def _handler(event: dict, context: object) -> dict:
    # 1. Parse trigger event
    detail = FinaliseReadyDetail.model_validate(event["detail"])
    doc_id = detail.docId

    redis = await get_redis()
    eb = EventBridgePublisher()

    # 2. Determine final status (read from compiled result or a status key)
    compiled_raw = await redis_get_json(redis, key_compiled(doc_id))
    status = "completed"
    if compiled_raw:
        compiled = CompiledResult.model_validate(compiled_raw)
        status = compiled.status

    # 3. Publish to SNS → front-end
    await _publish_sns(doc_id, status)

    # 4. Delete SQS message (pop the queue)
    receipt_handle = await redis_get_json(redis, key_receipt(doc_id))
    if receipt_handle:
        await _delete_sqs_message(receipt_handle)
    else:
        # Log warning — Stage 3 may not have stored the receipt handle correctly
        logger.warning("No SQS receipt handle found for docId=%s", doc_id)

    # 5. Clean up all Redis keys for this docId
    await _cleanup_redis(redis, doc_id)

    # 6. Publish PipelineComplete
    await eb.publish(
        detail_type="PipelineComplete",
        detail=PipelineCompleteDetail(docId=doc_id, status=status).model_dump(),
    )

    return {"statusCode": 200}
```

---

## SNS notification

```python
async def _publish_sns(doc_id: str, status: str) -> None:
    """Publish pipeline completion to SNS topic."""
    sns = boto3.client("sns")
    topic_arn = os.environ["SNS_TOPIC_ARN"]
    payload = json.dumps({"docId": doc_id, "status": status})
    # Run in executor — boto3 is synchronous
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: sns.publish(
        TopicArn=topic_arn,
        Message=payload,
        Subject=f"Pipeline {status}: {doc_id}",
    ))
```

SNS subscribers (configured outside this Lambda):
- Front-end webhook via SNS → API Gateway
- Email alerts for `status = "error"` (separate SNS filter policy)

---

## SQS message deletion

```python
async def _delete_sqs_message(receipt_handle: str) -> None:
    """Delete the SQS message — final acknowledgement of document processing."""
    sqs = boto3.client("sqs")
    queue_url = os.environ["SQS_QUEUE_URL"]
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: sqs.delete_message(
        QueueUrl=queue_url,
        ReceiptHandle=receipt_handle,
    ))
```

**Why this is the last step (after SNS):** The SQS message stays invisible in the
queue for the full pipeline duration. Deleting it is the acknowledgement that the
document was fully processed. If Stage 9 fails before deletion, the message becomes
visible again after the visibility timeout and the pipeline retries from Stage 3
(parse cache will hit, tagging cache will hit — only question resolution and compile
will rerun).

---

## Redis cleanup

```python
async def _cleanup_redis(redis: aioredis.Redis, doc_id: str) -> None:
    """Delete all Redis keys associated with this docId."""
    keys_to_delete = [
        key_sections(doc_id, "security"),
        key_sections(doc_id, "data"),
        key_sections(doc_id, "risk"),
        key_sections(doc_id, "ea"),
        key_sections(doc_id, "solution"),
        key_result(doc_id, "security"),
        key_result(doc_id, "data"),
        key_result(doc_id, "risk"),
        key_result(doc_id, "ea"),
        key_result(doc_id, "solution"),
        key_results_count(doc_id),
        key_compiled(doc_id),
        key_stage8_count(doc_id),
        key_receipt(doc_id),
    ]
    # Note: chunks:{hash} and tagged:{hash} are NOT deleted here — they are
    # content-addressed and shared across resubmissions (24h TTL handles expiry)
    await redis_delete_many(redis, *keys_to_delete)
```

---

## Idempotency

Stage 9 may be invoked twice if Stage 8 produces a duplicate `FinaliseReady` event
(e.g. due to a retry). Design defensively:

- SNS `publish` with the same payload twice is harmless (front-end deduplicates on `docId`)
- SQS `delete_message` on an already-deleted message returns a `ReceiptHandleIsInvalid` error — catch and log, do not raise
- Redis cleanup is idempotent (deleting non-existent keys is a no-op)
- `PipelineComplete` published twice is acceptable (CloudWatch will see two events, which is fine)

---

## Environment variables required

| Variable | Purpose |
|----------|---------|
| `SNS_TOPIC_ARN` | ARN of the notification SNS topic |
| `SQS_QUEUE_URL` | URL of the FIFO queue |
| `REDIS_HOST` | ElastiCache endpoint |

---

## Verification

```bash
python -c "from src.handlers.notify import lambda_handler"
python -m pytest tests/test_notify.py -v
ruff check src/handlers/notify.py
mypy src/handlers/notify.py
```

---

## Acceptance Criteria

- [ ] SNS published before SQS deletion (notification first, then acknowledgement)
- [ ] SQS `delete_message` called with receipt handle from Redis
- [ ] `ReceiptHandleIsInvalid` SQS error caught and logged (not raised)
- [ ] All docId-scoped Redis keys deleted; content-hash keys (`chunks:`, `tagged:`) preserved
- [ ] `PipelineComplete` event published with `docId` and `status`
- [ ] Unit tests: mock SNS, SQS, Redis; assert correct call order and cleanup
- [ ] `ruff check .` and `mypy src/` pass
