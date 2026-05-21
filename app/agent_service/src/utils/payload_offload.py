"""Inline-or-S3 payload offload helper for cross-stage handoffs."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

logger: logging.Logger = logging.getLogger(__name__)

# Default SQS inline threshold (bytes).
DEFAULT_INLINE_THRESHOLD: int = 240_000


def _serialise(payload: BaseModel | list[Any] | dict[str, Any]) -> bytes:
    """JSON-serialise a Pydantic model, list, or dict to UTF-8 bytes."""
    if isinstance(payload, BaseModel):
        return json.dumps(payload.model_dump(mode="json")).encode("utf-8")
    return json.dumps(payload).encode("utf-8")


def inline_or_s3(  # noqa: PLR0913
    payload: BaseModel | list[Any] | dict[str, Any],
    doc_id: str,
    stage: str,
    s3_client: Any,
    bucket: str,
    threshold: int = DEFAULT_INLINE_THRESHOLD,
) -> dict[str, Any]:
    """Return a payload envelope: inline JSON below the threshold, S3 above."""
    body: bytes = _serialise(payload)
    if len(body) <= threshold:
        return {"inline": body.decode("utf-8")}

    s3_key: str = f"state/{doc_id}/{stage}.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=body,
        ContentType="application/json",
    )
    logger.info(
        "Payload offloaded to S3: doc_id=%s stage=%s s3_key=%s size_bytes=%d",
        doc_id,
        stage,
        s3_key,
        len(body),
    )
    return {"s3Key": s3_key}


def resolve_payload(
    envelope: dict[str, Any],
    s3_client: Any,
    bucket: str,
) -> bytes:
    """Resolve an inline-or-S3 envelope to the underlying JSON bytes."""
    inline: str | None = envelope.get("inline")
    if inline is not None:
        return inline.encode("utf-8")

    s3_key: str | None = envelope.get("s3Key")
    if s3_key is not None:
        response: dict[str, Any] = s3_client.get_object(Bucket=bucket, Key=s3_key)
        body_bytes: bytes = response["Body"].read()
        return body_bytes

    raise ValueError(
        "Invalid payload envelope: must contain either 'inline' or 's3Key' "
        f"(got keys={sorted(envelope.keys())})"
    )
