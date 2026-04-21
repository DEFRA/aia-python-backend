# Plan 03 — Parse Lambda (Stage 3)

**Priority:** 3 (first Lambda in the processing chain)

**Depends on:** Plan 01 (src tree), Plan 02 (Redis + EventBridge utils)

---

## Goal

Implement `src/handlers/parse.py` — the Stage 3 Lambda that:

1. Is triggered by SQS (polling the FIFO queue fed by EventBridge on S3 upload)
2. Parses PDF or DOCX documents into chunks
3. Caches chunks in Redis keyed by `sha256(file_bytes)`
4. Stores the SQS receipt handle in Redis for Stage 9 cleanup
5. Publishes `DocumentParsed` to EventBridge

---

## Source material

The parsing logic (`extract_text_blocks`, `clean_and_chunk`) is fully specified in
`files/pdf_security_tagger.md`. Move/adapt this code into `src/handlers/parse.py`
or extract it into a dedicated `src/utils/document_parser.py` module.

---

## New file: `src/utils/document_parser.py`

Extract the pure parsing functions here so they can be unit-tested independently
of the Lambda handler:

```python
"""Document parsing utilities — PDF via pymupdf, DOCX via python-docx.

Both paths produce the same chunk schema:
    { chunk_index, page, is_heading, char_count, text }
"""
from __future__ import annotations

from pathlib import Path


def get_pdf_strategy(file_bytes: bytes) -> str:
    """Return 'text' if PDF has an extractable text layer, else 'vision'."""
    ...


def extract_text_blocks(file_bytes: bytes) -> list[dict]:
    """Extract raw text blocks from PDF bytes using pymupdf.

    Returns list of dicts: page, block_no, bbox, font_sizes, font_names, text.
    """
    ...


def clean_and_chunk(blocks: list[dict], max_chars: int = 1500) -> list[dict]:
    """Merge small PDF blocks into chunks with heading detection.

    Returns list of chunk dicts: chunk_index, page, is_heading, char_count, text.
    """
    ...


def parse_docx(file_bytes: bytes) -> list[dict]:
    """Parse a DOCX file to the same chunk schema as clean_and_chunk().

    Uses paragraph style names (Heading 1, Heading 2, Normal) for is_heading.
    Paragraph index is used as a proxy for page number.
    """
    ...
```

**Key differences from `pdf_security_tagger.md`:**
- Accept `bytes` not `Path` — Lambda downloads from S3 into memory
- `parse_docx` is a new addition (DOCX support specified in orchestration doc)
- Scanned PDF detection via `get_pdf_strategy()` — if `"vision"`, raise a
  `ScannedPdfError` for now (Textract integration is a future plan)

---

## `src/handlers/parse.py`

### SQS event schema (Lambda trigger)

```python
class SqsRecord(BaseModel):
    receiptHandle: str
    body: str   # JSON: { "s3Key": "...", "docId": "..." }

class SqsEvent(BaseModel):
    Records: list[SqsRecord]
```

Lambda processes **one record at a time** (SQS batch size = 1 on the FIFO queue).

### Handler flow

```python
async def _handler(event: dict, context: object) -> dict:
    # 1. Parse + validate SQS event
    sqs_event = SqsEvent.model_validate(event)
    record = sqs_event.Records[0]
    body = json.loads(record.body)
    doc_id: str = body["docId"]
    s3_key: str = body["s3Key"]
    receipt_handle: str = record.receiptHandle

    redis = await get_redis()
    eb = EventBridgePublisher()

    # 2. Download file bytes from S3
    file_bytes = await _download_s3(s3_key)
    content_hash = hashlib.sha256(file_bytes).hexdigest()

    # 3. Cache check
    chunks_key = key_chunks(content_hash)
    cached = await redis_get_json(redis, chunks_key)
    if cached is None:
        # 4. Parse document
        ext = Path(s3_key).suffix.lower()
        if ext == ".pdf":
            if get_pdf_strategy(file_bytes) == "vision":
                raise ScannedPdfError(f"{s3_key} has no text layer")
            blocks = extract_text_blocks(file_bytes)
            chunks = clean_and_chunk(blocks)
        elif ext == ".docx":
            chunks = parse_docx(file_bytes)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

        # 5. Write chunks to Redis
        await redis_set_json(redis, chunks_key, chunks, TTL_CHUNKS)

    # 6. Store SQS receipt handle (TTL = SQS visibility timeout, e.g. 900s)
    receipt_key = key_receipt(doc_id)
    await redis_set_json(redis, receipt_key, receipt_handle, ttl=900)

    # 7. Publish DocumentParsed
    await eb.publish(
        detail_type="DocumentParsed",
        detail=DocumentParsedDetail(
            docId=doc_id,
            chunksCacheKey=chunks_key,
            contentHash=content_hash,
        ).model_dump(),
    )

    return {"statusCode": 200}
```

### Error handling

| Error | Action |
|-------|--------|
| `ScannedPdfError` | Let exception propagate → Lambda DLQ after N retries |
| S3 download failure | Propagate → Lambda retry |
| Redis write failure | Propagate → Lambda retry (idempotent: same content hash) |
| EventBridge publish failure | Propagate → Lambda retry |

Do **not** catch and swallow errors — SQS visibility timeout + DLQ handle retries.

---

## New exception: `src/utils/exceptions.py`

```python
class ScannedPdfError(RuntimeError):
    """Raised when a PDF has no extractable text layer."""
```

---

## CloudWatch metric

After a successful parse, emit a custom metric:

```python
cloudwatch.put_metric_data(
    Namespace="DefraP pipeline",
    MetricData=[{
        "MetricName": "ParseDuration",
        "Value": elapsed_ms,
        "Unit": "Milliseconds",
    }]
)
```

---

## Verification

```bash
python -c "from src.handlers.parse import lambda_handler"
python -c "from src.utils.document_parser import extract_text_blocks, clean_and_chunk, parse_docx"
python -m pytest tests/test_parse.py -v
ruff check src/handlers/parse.py src/utils/document_parser.py
mypy src/handlers/parse.py src/utils/document_parser.py
```

---

## Acceptance Criteria

- [ ] `src/utils/document_parser.py` with `extract_text_blocks`, `clean_and_chunk`, `parse_docx`, `get_pdf_strategy`
- [ ] `src/utils/exceptions.py` with `ScannedPdfError`
- [ ] `src/handlers/parse.py` with full `lambda_handler` → `_handler` implementation
- [ ] SQS receipt handle written to Redis with correct key and TTL
- [ ] Chunks cached by `sha256(file_bytes)` — resubmission skips parse
- [ ] `DocumentParsed` event published with `chunksCacheKey` and `contentHash`
- [ ] Unit tests for `extract_text_blocks` and `clean_and_chunk` with a sample PDF fixture
- [ ] `ruff check .` and `mypy src/` pass
