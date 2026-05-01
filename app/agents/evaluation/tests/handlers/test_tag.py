"""Tests for the Stage 4 Tag Lambda handler (Plan 11)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.agents.schemas import DocumentParsedDetail


def _make_event(
    doc_id: str = "doc-001",
    inline_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a minimal EventBridge event dict for Stage 4."""
    chunks: list[dict[str, Any]] = inline_chunks if inline_chunks is not None else _sample_chunks()
    detail: DocumentParsedDetail = DocumentParsedDetail.model_validate(
        {"document_id": doc_id, "payload": {"inline": json.dumps(chunks)}}
    )
    return {"detail": detail.model_dump(by_alias=True)}


def _sample_chunks() -> list[dict[str, Any]]:
    """Return sample parsed chunks (Stage 3 output)."""
    return [
        {
            "chunk_index": 0,
            "page": 1,
            "is_heading": False,
            "char_count": 20,
            "text": "MFA is enforced for all users.",
        },
    ]


def _sample_tagged() -> list[dict[str, Any]]:
    """Return sample tagged chunk dicts."""
    return [
        {
            "chunk_index": 0,
            "page": 1,
            "is_heading": False,
            "text": "MFA is enforced for all users.",
            "relevant": True,
            "tags": ["authentication"],
            "reason": "Covers MFA.",
        },
    ]


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_validates_event() -> None:
    """_handler should reject an event missing required detail fields."""
    from src.handlers.tag import _handler

    bad_event: dict[str, Any] = {"detail": {"document_id": "doc-001"}}

    with pytest.raises(ValidationError):
        await _handler(bad_event, {})


@pytest.mark.asyncio
async def test_tag_handler_resolves_inline_chunks() -> None:
    """The handler reads chunks via resolve_payload from the inline envelope."""
    event: dict[str, Any] = _make_event()

    published: list[dict[str, Any]] = []

    async def _mock_publish(detail_type: str, detail: dict[str, Any]) -> None:
        published.append({"detail_type": detail_type, "detail": detail})

    mock_publisher: MagicMock = MagicMock()
    mock_publisher.publish = _mock_publish

    from src.agents.schemas import TaggedChunk

    mock_tagged: list[TaggedChunk] = [TaggedChunk.model_validate(t) for t in _sample_tagged()]
    mock_agent: MagicMock = MagicMock()
    mock_agent.tag = AsyncMock(return_value=mock_tagged)

    with (
        patch("src.handlers.tag._get_publisher", return_value=mock_publisher),
        patch("src.handlers.tag._get_s3", return_value=MagicMock()),
        patch("src.handlers.tag.TaggingAgent", return_value=mock_agent),
        patch("src.handlers.tag.anthropic"),
        patch("src.handlers.tag._emit_metric", new_callable=AsyncMock),
        patch.dict("os.environ", {"S3_BUCKET": "test-bucket"}),
    ):
        from src.handlers.tag import _handler

        result: dict[str, Any] = await _handler(event, {})

    expected_status: int = 200
    assert result["statusCode"] == expected_status
    # Tagging agent ran
    mock_agent.tag.assert_called_once()
    # Inline payload published downstream
    assert len(published) == 1
    assert published[0]["detail_type"] == "DocumentTagged"
    detail: dict[str, Any] = published[0]["detail"]
    assert "payload" in detail
    assert "inline" in detail["payload"]


@pytest.mark.asyncio
async def test_tag_handler_resolves_s3_chunks() -> None:
    """When the parsed-event envelope is s3Key, the handler downloads from S3."""
    chunks_json: bytes = json.dumps(_sample_chunks()).encode("utf-8")

    detail = DocumentParsedDetail.model_validate(
        {"document_id": "doc-s3", "payload": {"s3Key": "state/doc-s3/chunks.json"}}
    )
    event: dict[str, Any] = {"detail": detail.model_dump(by_alias=True)}

    body_obj: Any = MagicMock()
    body_obj.read.return_value = chunks_json
    s3_client: Any = MagicMock()
    s3_client.get_object.return_value = {"Body": body_obj}

    async def _mock_publish(detail_type: str, detail: dict[str, Any]) -> None:
        pass

    mock_publisher: MagicMock = MagicMock()
    mock_publisher.publish = _mock_publish

    from src.agents.schemas import TaggedChunk

    mock_tagged: list[TaggedChunk] = [TaggedChunk.model_validate(t) for t in _sample_tagged()]
    mock_agent: MagicMock = MagicMock()
    mock_agent.tag = AsyncMock(return_value=mock_tagged)

    with (
        patch("src.handlers.tag._get_publisher", return_value=mock_publisher),
        patch("src.handlers.tag._get_s3", return_value=s3_client),
        patch("src.handlers.tag.TaggingAgent", return_value=mock_agent),
        patch("src.handlers.tag.anthropic"),
        patch("src.handlers.tag._emit_metric", new_callable=AsyncMock),
        patch.dict("os.environ", {"S3_BUCKET": "test-bucket"}),
    ):
        from src.handlers.tag import _handler

        await _handler(event, {})

    s3_client.get_object.assert_called_once_with(
        Bucket="test-bucket", Key="state/doc-s3/chunks.json"
    )
    mock_agent.tag.assert_called_once()


@pytest.mark.asyncio
async def test_tag_handler_offloads_large_tagged_payload() -> None:
    """When the tagged output exceeds the threshold, the event uses an s3Key envelope."""
    event: dict[str, Any] = _make_event()

    big_tagged_dicts: list[dict[str, Any]] = [
        {
            "chunk_index": i,
            "page": 1,
            "is_heading": False,
            "text": "x" * 1000,
            "relevant": True,
            "tags": ["authentication"],
            "reason": "padding",
        }
        for i in range(300)
    ]

    from src.agents.schemas import TaggedChunk

    big_tagged: list[TaggedChunk] = [TaggedChunk.model_validate(t) for t in big_tagged_dicts]

    mock_agent: MagicMock = MagicMock()
    mock_agent.tag = AsyncMock(return_value=big_tagged)

    s3_client: Any = MagicMock()

    published: list[dict[str, Any]] = []

    async def _mock_publish(detail_type: str, detail: dict[str, Any]) -> None:
        published.append({"detail_type": detail_type, "detail": detail})

    mock_publisher: MagicMock = MagicMock()
    mock_publisher.publish = _mock_publish

    with (
        patch("src.handlers.tag._get_publisher", return_value=mock_publisher),
        patch("src.handlers.tag._get_s3", return_value=s3_client),
        patch("src.handlers.tag.TaggingAgent", return_value=mock_agent),
        patch("src.handlers.tag.anthropic"),
        patch("src.handlers.tag._emit_metric", new_callable=AsyncMock),
        patch.dict("os.environ", {"S3_BUCKET": "test-bucket"}),
    ):
        from src.handlers.tag import _handler

        await _handler(event, {})

    assert len(published) == 1
    assert published[0]["detail"]["payload"]["s3Key"] == "state/doc-001/tagged.json"
    s3_client.put_object.assert_called_once()


@pytest.mark.asyncio
async def test_handler_emits_metrics() -> None:
    """Handler should emit TaggingDuration and TaggedChunkCount metrics."""
    event: dict[str, Any] = _make_event()

    async def _mock_publish(detail_type: str, detail: dict[str, Any]) -> None:
        pass

    mock_publisher: MagicMock = MagicMock()
    mock_publisher.publish = _mock_publish

    from src.agents.schemas import TaggedChunk

    mock_tagged: list[TaggedChunk] = [TaggedChunk.model_validate(t) for t in _sample_tagged()]
    mock_agent: MagicMock = MagicMock()
    mock_agent.tag = AsyncMock(return_value=mock_tagged)

    mock_emit: AsyncMock = AsyncMock()

    with (
        patch("src.handlers.tag._get_publisher", return_value=mock_publisher),
        patch("src.handlers.tag._get_s3", return_value=MagicMock()),
        patch("src.handlers.tag.TaggingAgent", return_value=mock_agent),
        patch("src.handlers.tag.anthropic"),
        patch("src.handlers.tag._emit_metric", mock_emit),
        patch.dict("os.environ", {"S3_BUCKET": "test-bucket"}),
    ):
        from src.handlers.tag import _handler

        await _handler(event, {})

    expected_metric_calls: int = 2
    assert mock_emit.call_count == expected_metric_calls
    call_names: list[str] = [call.args[0] for call in mock_emit.call_args_list]
    assert "TaggingDuration" in call_names
    assert "TaggedChunkCount" in call_names
