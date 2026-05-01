"""Stage 3 -- Parse Lambda handler.

Triggered by SQS (polling).  Downloads a PDF or DOCX from S3, parses it into
chunks, and publishes a ``DocumentParsed`` event whose ``payload`` envelope
either inlines the chunks (small documents) or references an S3 offload key
(large documents).  No Redis state is kept.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import boto3
from pydantic import BaseModel

from src.agents.schemas import DocumentParsedDetail
from src.config import CloudWatchConfig, EventBridgeConfig
from src.utils.document_parser import (
    clean_and_chunk,
    extract_text_blocks,
    get_pdf_strategy,
    parse_docx,
)
from src.utils.eventbridge import EventBridgePublisher
from src.utils.exceptions import ScannedPdfError
from src.utils.payload_offload import inline_or_s3

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# SQS event Pydantic models
# ---------------------------------------------------------------------------


class SqsRecordBody(BaseModel):
    """JSON body inside each SQS record."""

    document_id: str
    s3Key: str


class SqsRecord(BaseModel):
    """A single SQS record from the Lambda event."""

    receiptHandle: str
    body: str  # JSON string containing SqsRecordBody


class SqsEvent(BaseModel):
    """Top-level SQS event envelope."""

    Records: list[SqsRecord]


# ---------------------------------------------------------------------------
# Module-level singletons (cold-start reuse)
# ---------------------------------------------------------------------------

_publisher: EventBridgePublisher | None = None
_s3: Any = None
_cw: Any = None
_cw_config: CloudWatchConfig | None = None


def _get_cw_config() -> CloudWatchConfig:
    """Return the module-level CloudWatchConfig singleton, creating on first call."""
    global _cw_config  # noqa: PLW0603
    if _cw_config is None:
        _cw_config = CloudWatchConfig()
    return _cw_config


def _get_publisher() -> EventBridgePublisher:
    """Return the module-level EventBridgePublisher singleton, creating on first call."""
    global _publisher  # noqa: PLW0603
    if _publisher is None:
        _publisher = EventBridgePublisher(EventBridgeConfig())
    return _publisher


def _get_s3() -> Any:
    """Return the module-level S3 client singleton, creating on first call."""
    global _s3  # noqa: PLW0603
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _get_cw() -> Any:
    """Return the module-level CloudWatch client singleton, creating on first call."""
    global _cw  # noqa: PLW0603
    if _cw is None:
        _cw = boto3.client("cloudwatch")
    return _cw


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _download_s3(s3_client: Any, bucket: str, key: str) -> bytes:
    """Download an object from S3 via ``run_in_executor``.

    Args:
        s3_client: A boto3 S3 client.
        bucket: S3 bucket name.
        key: S3 object key.

    Returns:
        Raw file bytes.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    response: dict[str, Any] = await loop.run_in_executor(
        None,
        lambda: s3_client.get_object(Bucket=bucket, Key=key),
    )
    body_bytes: bytes = await loop.run_in_executor(None, response["Body"].read)
    return body_bytes


async def _emit_metric(name: str, value: float, unit: str = "Milliseconds") -> None:
    """Emit a CloudWatch metric via ``run_in_executor``.

    Args:
        name: Metric name (e.g. ``"ParseDuration"``).
        value: Metric value.
        unit: CloudWatch unit string.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: _get_cw().put_metric_data(
            Namespace=_get_cw_config().namespace,
            MetricData=[{"MetricName": name, "Value": value, "Unit": unit}],
        ),
    )


def _parse_bytes(file_bytes: bytes, s3_key: str, doc_id: str) -> list[dict[str, Any]]:
    """Parse a PDF or DOCX byte stream into chunks.

    Args:
        file_bytes: Raw document bytes.
        s3_key: The original S3 key — used for the file extension.
        doc_id: The document ID — used for error messages.

    Returns:
        A list of chunk dicts (the document_parser chunk schema).

    Raises:
        ScannedPdfError: If the PDF has no extractable text layer.
        ValueError: If the file extension is unsupported.
    """
    extension: str = s3_key.rsplit(".", maxsplit=1)[-1].lower() if "." in s3_key else ""

    if extension == "pdf":
        strategy: str = get_pdf_strategy(file_bytes)
        if strategy == "vision":
            raise ScannedPdfError(
                f"PDF has no extractable text layer: doc_id={doc_id} s3_key={s3_key}"
            )
        blocks: list[dict[str, Any]] = extract_text_blocks(file_bytes)
        return clean_and_chunk(blocks)

    if extension == "docx":
        return parse_docx(file_bytes)

    raise ValueError(f"Unsupported file extension: '{extension}' for s3_key={s3_key}")


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point -- delegates to async core."""
    return asyncio.run(_handler(event, context))


async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Async core of the Stage 3 Parse handler.

    Flow:
        1. Validate SQS event via Pydantic.
        2. Download file bytes from S3.
        3. Parse PDF or DOCX into chunks (no cache; every invocation parses fresh).
        4. Build the inline-or-S3 payload envelope.
        5. Publish ``DocumentParsed`` event with the envelope.
        6. Emit ``ParseDuration`` CloudWatch metric.

    Args:
        event: Raw SQS Lambda event dict.
        context: Lambda context object (unused).

    Returns:
        Dict with ``statusCode`` 200 on success.

    Raises:
        ScannedPdfError: If the PDF has no extractable text layer.
        ValueError: If the file extension is unsupported.
    """
    start: float = time.monotonic()

    # 1. Validate SQS event
    sqs_event: SqsEvent = SqsEvent.model_validate(event)
    record: SqsRecord = sqs_event.Records[0]
    body: SqsRecordBody = SqsRecordBody.model_validate_json(record.body)
    doc_id: str = body.document_id
    s3_key: str = body.s3Key

    logger.info("Stage 3 Parse: doc_id=%s s3_key=%s", doc_id, s3_key)

    # 2. Download file from S3
    bucket: str = os.environ["S3_BUCKET"]
    file_bytes: bytes = await _download_s3(_get_s3(), bucket, s3_key)

    # 3. Parse
    chunks: list[dict[str, Any]] = _parse_bytes(file_bytes, s3_key, doc_id)
    logger.info("Parsed %d chunks: doc_id=%s", len(chunks), doc_id)

    # 4. Build payload envelope (inline if small, S3 if large)
    envelope: dict[str, Any] = inline_or_s3(
        payload=chunks,
        doc_id=doc_id,
        stage="chunks",
        s3_client=_get_s3(),
        bucket=bucket,
    )

    # 5. Publish DocumentParsed event
    detail: DocumentParsedDetail = DocumentParsedDetail.model_validate(
        {"document_id": doc_id, "payload": envelope}
    )
    await _get_publisher().publish("DocumentParsed", detail.model_dump(by_alias=True))

    # 6. Emit metric
    duration_ms: float = (time.monotonic() - start) * 1000
    await _emit_metric("ParseDuration", duration_ms)

    logger.info("Stage 3 complete: doc_id=%s duration_ms=%.1f", doc_id, duration_ms)
    return {"statusCode": 200}
