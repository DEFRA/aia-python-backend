"""Inline-or-S3 payload offload helper for cross-stage handoffs.

Pipeline stages pass intermediate state (parsed chunks, tagged chunks, sections)
through small EventBridge / SQS messages.  When a payload fits within the SQS
inline limit (240 KB by default) it is JSON-serialised inline; otherwise it is
written to ``s3://{bucket}/state/{docId}/{stage}.json`` and the receiving
handler dereferences the ``s3Key`` to fetch the bytes.

The corresponding Pydantic discriminated union ``PayloadEnvelope`` lives in
``src.agents.schemas`` so EventBridge ``detail`` models can validate the
envelope shape at the boundary.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

logger: logging.Logger = logging.getLogger(__name__)

# Default SQS inline threshold (bytes).  Leaves headroom under the 256 KB SQS
# message size limit for envelope JSON overhead.
DEFAULT_INLINE_THRESHOLD: int = 240_000


def _serialise(payload: BaseModel | list[Any] | dict[str, Any]) -> bytes:
    """JSON-serialise a Pydantic model, list, or dict to UTF-8 bytes.

    Pydantic models are dumped via ``model_dump(mode="json")`` so nested models
    convert recursively.  Lists / dicts are passed through ``json.dumps``.

    Args:
        payload: The object to serialise.

    Returns:
        The UTF-8 encoded JSON bytes.
    """
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
    """Return a payload envelope: inline JSON below the threshold, S3 above.

    Args:
        payload: The object to carry across stages.
        doc_id: The document ID — used to build the S3 key on offload.
        stage: A short tag identifying the pipeline stage (e.g. ``"chunks"``,
            ``"tagged"``).  Becomes part of the S3 key.
        s3_client: A boto3 S3 client.
        bucket: The S3 bucket to write to.
        threshold: Maximum inline size in bytes (default 240,000).

    Returns:
        Either ``{"inline": "<json string>"}`` or ``{"s3Key": "state/<docId>/<stage>.json"}``.
    """
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
    """Resolve an inline-or-S3 envelope to the underlying JSON bytes.

    Args:
        envelope: A dict with exactly one of ``inline`` or ``s3Key``.
        s3_client: A boto3 S3 client (used only on the S3 branch).
        bucket: The S3 bucket to read from.

    Returns:
        The UTF-8 encoded JSON bytes of the payload.

    Raises:
        ValueError: If the envelope contains neither ``inline`` nor ``s3Key``.
    """
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
