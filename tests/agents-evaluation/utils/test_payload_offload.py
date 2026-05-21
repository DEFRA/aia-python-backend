"""Tests for the payload_offload helper."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from src.utils.payload_offload import inline_or_s3, resolve_payload


class _Sample(BaseModel):
    """Tiny pydantic model for serialisation tests."""

    name: str
    value: int


def test_inline_or_s3_inlines_small_payload() -> None:
    """Payloads under the threshold are inlined; no S3 write."""
    s3: Any = MagicMock()
    payload: _Sample = _Sample(name="hello", value=1)

    envelope: dict[str, Any] = inline_or_s3(
        payload=payload,
        doc_id="doc-1",
        stage="chunks",
        s3_client=s3,
        bucket="my-bucket",
    )

    assert "inline" in envelope
    assert "s3Key" not in envelope
    s3.put_object.assert_not_called()


def test_inline_or_s3_offloads_large_payload() -> None:
    """Payloads over the threshold are offloaded to S3 and reported via s3Key."""
    s3: Any = MagicMock()
    big_payload: list[dict[str, str]] = [{"text": "x" * 1000} for _ in range(300)]  # > 240 KB

    envelope: dict[str, Any] = inline_or_s3(
        payload=big_payload,
        doc_id="doc-2",
        stage="chunks",
        s3_client=s3,
        bucket="my-bucket",
    )

    assert "s3Key" in envelope
    assert "inline" not in envelope
    assert envelope["s3Key"] == "state/doc-2/chunks.json"
    s3.put_object.assert_called_once()
    kwargs: dict[str, Any] = s3.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "my-bucket"
    assert kwargs["Key"] == "state/doc-2/chunks.json"


def test_resolve_payload_returns_inline_bytes() -> None:
    """An inline envelope yields its serialised bytes without S3 access."""
    s3: Any = MagicMock()
    inline_bytes: bytes = json.dumps({"foo": "bar"}).encode("utf-8")
    envelope: dict[str, Any] = {"inline": inline_bytes.decode("utf-8")}

    out: bytes = resolve_payload(envelope, s3_client=s3, bucket="my-bucket")

    assert out == inline_bytes
    s3.get_object.assert_not_called()


def test_resolve_payload_fetches_from_s3_for_s3key_envelope() -> None:
    """An s3Key envelope reads the object from S3 and returns its bytes."""
    payload_bytes: bytes = json.dumps([{"a": 1}]).encode("utf-8")
    s3: Any = MagicMock()
    body: Any = MagicMock()
    body.read.return_value = payload_bytes
    s3.get_object.return_value = {"Body": body}

    envelope: dict[str, Any] = {"s3Key": "state/doc-3/tagged.json"}
    out: bytes = resolve_payload(envelope, s3_client=s3, bucket="my-bucket")

    assert out == payload_bytes
    s3.get_object.assert_called_once_with(Bucket="my-bucket", Key="state/doc-3/tagged.json")


def test_resolve_payload_rejects_envelope_with_neither_field() -> None:
    """A malformed envelope with neither inline nor s3Key raises ValueError."""
    s3: Any = MagicMock()
    with pytest.raises(ValueError, match="payload envelope"):
        resolve_payload({}, s3_client=s3, bucket="my-bucket")
