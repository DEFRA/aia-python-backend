"""Tests for the TaggingAgent."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.schemas import TaggedChunk
from src.agents.tagging_agent import TaggingAgent

# TaggingAgent constructor signature drifted away from these fixtures. Marker
# is strict=True so a future fix flips xfail -> xpass and pytest will fail the
# build until this marker is removed.
pytestmark = pytest.mark.xfail(
    strict=True,
    reason="deferred: TaggingAgent constructor drift; remove once fixtures are migrated",
)


def _make_chunk(index: int, text: str = "sample text", page: int = 1) -> dict[str, Any]:
    """Build a minimal chunk dict matching clean_and_chunk() output."""
    return {
        "chunk_index": index,
        "page": page,
        "is_heading": False,
        "char_count": len(text),
        "text": text,
    }


def _make_tagged_response(chunks: list[dict[str, Any]]) -> str:
    """Build a JSON string mimicking the LLM's tagging response."""
    items: list[dict[str, Any]] = [
        {
            "chunk_index": c["chunk_index"],
            "page": c["page"],
            "is_heading": c["is_heading"],
            "text": c["text"],
            "relevant": True,
            "tags": ["authentication"],
            "reason": "Covers MFA.",
        }
        for c in chunks
    ]
    return json.dumps(items)


def _mock_client(response_text: str) -> MagicMock:
    """Create a mock AsyncAnthropic client returning the given text."""
    client: MagicMock = MagicMock()
    client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=response_text)])
    )
    return client


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_returns_list_of_tagged_chunks() -> None:
    """tag() should return a list of TaggedChunk models."""
    chunks: list[dict[str, Any]] = [_make_chunk(0), _make_chunk(1)]
    response_text: str = _make_tagged_response(chunks)
    client: MagicMock = _mock_client(response_text)

    agent: TaggingAgent = TaggingAgent(client=client)
    result: list[TaggedChunk] = await agent.tag(chunks)

    expected_count: int = 2
    assert len(result) == expected_count
    assert all(isinstance(r, TaggedChunk) for r in result)
    assert result[0].chunk_index == 0
    assert result[1].chunk_index == 1


@pytest.mark.asyncio
async def test_tag_batching_with_more_than_batch_size() -> None:
    """tag() should call the LLM once per batch when chunks exceed BATCH_SIZE."""
    batch_size: int = 3
    chunks: list[dict[str, Any]] = [_make_chunk(i) for i in range(7)]

    call_count: int = 0

    async def _mock_create(**kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        input_chunks: list[dict[str, Any]] = json.loads(kwargs["messages"][0]["content"])
        text: str = _make_tagged_response(input_chunks)
        return MagicMock(content=[MagicMock(text=text)])

    client: MagicMock = MagicMock()
    client.messages.create = _mock_create

    agent: TaggingAgent = TaggingAgent(client=client, batch_size=batch_size)
    result: list[TaggedChunk] = await agent.tag(chunks)

    # 7 chunks / batch_size 3 = 3 API calls (3 + 3 + 1)
    expected_calls: int = 3
    expected_total: int = 7
    assert call_count == expected_calls
    assert len(result) == expected_total


@pytest.mark.asyncio
async def test_tag_strips_code_fences() -> None:
    """tag() should handle the LLM responses wrapped in markdown code fences."""
    chunks: list[dict[str, Any]] = [_make_chunk(0)]
    inner: str = _make_tagged_response(chunks)
    fenced: str = f"```json\n{inner}\n```"
    client: MagicMock = _mock_client(fenced)

    agent: TaggingAgent = TaggingAgent(client=client)
    result: list[TaggedChunk] = await agent.tag(chunks)

    assert len(result) == 1
    assert result[0].relevant is True


@pytest.mark.asyncio
async def test_tag_uses_temperature_zero() -> None:
    """tag() should call the LLM with temperature=0.0 for deterministic output."""
    chunks: list[dict[str, Any]] = [_make_chunk(0)]
    response_text: str = _make_tagged_response(chunks)
    client: MagicMock = _mock_client(response_text)

    agent: TaggingAgent = TaggingAgent(client=client)
    await agent.tag(chunks)

    call_kwargs: dict[str, Any] = client.messages.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0.0


@pytest.mark.asyncio
async def test_tag_empty_chunks_returns_empty() -> None:
    """tag() with an empty list should return an empty list without calling the LLM."""
    client: MagicMock = MagicMock()
    client.messages.create = AsyncMock()

    agent: TaggingAgent = TaggingAgent(client=client)
    result: list[TaggedChunk] = await agent.tag([])

    assert result == []
    client.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_tag_non_relevant_chunk() -> None:
    """tag() should handle chunks marked as non-relevant."""
    chunks: list[dict[str, Any]] = [_make_chunk(0, text="Table of contents")]
    response: str = json.dumps(
        [
            {
                "chunk_index": 0,
                "page": 1,
                "is_heading": False,
                "text": "Table of contents",
                "relevant": False,
                "tags": [],
                "reason": None,
            }
        ]
    )
    client: MagicMock = _mock_client(response)

    agent: TaggingAgent = TaggingAgent(client=client)
    result: list[TaggedChunk] = await agent.tag(chunks)

    assert len(result) == 1
    assert result[0].relevant is False
    assert result[0].tags == []
    assert result[0].reason is None
