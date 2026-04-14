"""Tests for the Stage 4 Tag Lambda handler."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis as fakeredis
import pytest
from pydantic import ValidationError

from src.agents.schemas import DocumentParsedDetail


def _make_event(
    doc_id: str = "doc-001",
    content_hash: str = "abc123",
) -> dict[str, Any]:
    """Build a minimal EventBridge event dict for Stage 4."""
    detail: DocumentParsedDetail = DocumentParsedDetail(
        docId=doc_id,
        chunksCacheKey=f"chunks:{content_hash}",
        contentHash=content_hash,
    )
    return {"detail": detail.model_dump(by_alias=True)}


def _sample_chunks() -> list[dict[str, Any]]:
    """Return sample parsed chunks as stored in Redis."""
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
    """Return sample tagged chunk dicts as stored in Redis."""
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


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    """Create a fresh fake Redis instance per test."""
    return fakeredis.FakeRedis(decode_responses=True)


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_validates_event() -> None:
    """_handler should reject an event missing required detail fields."""
    from src.handlers.tag import _handler

    bad_event: dict[str, Any] = {"detail": {"docId": "doc-001"}}

    with pytest.raises(ValidationError):
        await _handler(bad_event, {})


@pytest.mark.asyncio
async def test_cache_hit_skips_claude(fake_redis: fakeredis.FakeRedis) -> None:
    """On a tagged cache hit, handler should skip Claude and still publish."""
    event: dict[str, Any] = _make_event()
    content_hash: str = "abc123"

    # Pre-populate tagged cache
    await fake_redis.setex(
        f"tagged:{content_hash}",
        86_400,
        json.dumps(_sample_tagged()),
    )

    published: list[dict[str, Any]] = []

    async def _mock_publish(detail_type: str, detail: dict[str, Any]) -> None:
        published.append({"detail_type": detail_type, "detail": detail})

    mock_publisher: MagicMock = MagicMock()
    mock_publisher.publish = _mock_publish

    with (
        patch("src.handlers.tag.get_redis", return_value=fake_redis),
        patch("src.handlers.tag._get_redis_config", return_value=MagicMock()),
        patch("src.handlers.tag._get_publisher", return_value=mock_publisher),
    ):
        from src.handlers.tag import _handler

        result: dict[str, Any] = await _handler(event, {})

    expected_status: int = 200
    assert result["statusCode"] == expected_status
    assert len(published) == 1
    assert published[0]["detail_type"] == "DocumentTagged"


@pytest.mark.asyncio
async def test_cache_miss_calls_agent_and_caches(fake_redis: fakeredis.FakeRedis) -> None:
    """On a cache miss, handler should call TaggingAgent, cache results, and publish."""
    event: dict[str, Any] = _make_event()
    content_hash: str = "abc123"

    # Pre-populate chunks (Stage 3 output)
    await fake_redis.setex(
        f"chunks:{content_hash}",
        86_400,
        json.dumps(_sample_chunks()),
    )

    published: list[dict[str, Any]] = []

    async def _mock_publish(detail_type: str, detail: dict[str, Any]) -> None:
        published.append({"detail_type": detail_type, "detail": detail})

    mock_publisher: MagicMock = MagicMock()
    mock_publisher.publish = _mock_publish

    # Mock the TaggingAgent
    from src.agents.schemas import TaggedChunk

    mock_tagged: list[TaggedChunk] = [TaggedChunk.model_validate(t) for t in _sample_tagged()]

    mock_agent: MagicMock = MagicMock()
    mock_agent.tag = AsyncMock(return_value=mock_tagged)

    with (
        patch("src.handlers.tag.get_redis", return_value=fake_redis),
        patch("src.handlers.tag._get_redis_config", return_value=MagicMock()),
        patch("src.handlers.tag._get_publisher", return_value=mock_publisher),
        patch("src.handlers.tag.TaggingAgent", return_value=mock_agent),
        patch("src.handlers.tag.anthropic"),
        patch("src.handlers.tag._emit_metric", new_callable=AsyncMock),
    ):
        from src.handlers.tag import _handler

        result: dict[str, Any] = await _handler(event, {})

    expected_status: int = 200
    assert result["statusCode"] == expected_status

    # Verify agent was called
    mock_agent.tag.assert_called_once()

    # Verify result was cached in Redis
    cached_raw: str | None = await fake_redis.get(f"tagged:{content_hash}")
    assert cached_raw is not None
    cached: list[dict[str, Any]] = json.loads(cached_raw)
    assert len(cached) == 1
    assert cached[0]["tags"] == ["authentication"]

    # Verify event was published
    assert len(published) == 1
    assert published[0]["detail_type"] == "DocumentTagged"
    assert published[0]["detail"]["contentHash"] == content_hash


@pytest.mark.asyncio
async def test_chunks_cache_miss_raises(fake_redis: fakeredis.FakeRedis) -> None:
    """Handler should raise RuntimeError if chunks are not in Redis."""
    event: dict[str, Any] = _make_event()

    with (
        patch("src.handlers.tag.get_redis", return_value=fake_redis),
        patch("src.handlers.tag._get_redis_config", return_value=MagicMock()),
    ):
        from src.handlers.tag import _handler

        with pytest.raises(RuntimeError, match="Chunks cache miss"):
            await _handler(event, {})


@pytest.mark.asyncio
async def test_handler_emits_metrics_on_cache_miss(
    fake_redis: fakeredis.FakeRedis,
) -> None:
    """Handler should emit TaggingDuration and TaggedChunkCount metrics."""
    event: dict[str, Any] = _make_event()
    content_hash: str = "abc123"

    await fake_redis.setex(
        f"chunks:{content_hash}",
        86_400,
        json.dumps(_sample_chunks()),
    )

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
        patch("src.handlers.tag.get_redis", return_value=fake_redis),
        patch("src.handlers.tag._get_redis_config", return_value=MagicMock()),
        patch("src.handlers.tag._get_publisher", return_value=mock_publisher),
        patch("src.handlers.tag.TaggingAgent", return_value=mock_agent),
        patch("src.handlers.tag.anthropic"),
        patch("src.handlers.tag._emit_metric", mock_emit),
    ):
        from src.handlers.tag import _handler

        await _handler(event, {})

    # Should have been called twice: TaggingDuration + TaggedChunkCount
    expected_metric_calls: int = 2
    assert mock_emit.call_count == expected_metric_calls
    call_names: list[str] = [call.args[0] for call in mock_emit.call_args_list]
    assert "TaggingDuration" in call_names
    assert "TaggedChunkCount" in call_names
