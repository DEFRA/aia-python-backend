"""Retry behaviour tests for ``TaggingAgent._tag_batch``.

Verifies that the ``@agent_retry()`` decorator applied to ``_tag_batch``
allows a single failed batch to retry independently of other batches.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from anthropic import RateLimitError
from pydantic import ValidationError

from src.agents.schemas import TaggedChunk
from src.agents.tagging_agent import TaggingAgent
from src.config import TaggingAgentConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _make_rate_limit_error() -> RateLimitError:
    response: httpx.Response = httpx.Response(status_code=429, request=_make_request())
    return RateLimitError(message="rate limited", response=response, body=None)


def _make_chunk(index: int, text: str = "sample text", page: int = 1) -> dict[str, Any]:
    return {
        "chunk_index": index,
        "page": page,
        "is_heading": False,
        "char_count": len(text),
        "text": text,
    }


def _make_tagged_response_text(chunks: list[dict[str, Any]]) -> str:
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


def _make_response(text: str) -> MagicMock:
    response: MagicMock = MagicMock()
    response.content = [MagicMock(text=text)]
    return response


def _make_agent(client: MagicMock, batch_size: int = 15) -> TaggingAgent:
    config: TaggingAgentConfig = TaggingAgentConfig(
        TAGGING_MODEL="test-model",
        TAGGING_BATCH_SIZE=batch_size,
        ANTHROPIC_API_KEY="test-key",  # pragma: allowlist secret
    )
    return TaggingAgent(client=client, config=config)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch asyncio.sleep so tenacity backoffs run instantly during retries."""
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tagging_agent_retries_one_failed_batch_only() -> None:
    """A failure in batch 1 retries only batch 1 — batch 2 still runs once."""
    chunks: list[dict[str, Any]] = [_make_chunk(i) for i in range(4)]
    batch1: list[dict[str, Any]] = chunks[:2]
    batch2: list[dict[str, Any]] = chunks[2:]

    client: MagicMock = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=[
            _make_rate_limit_error(),  # batch 1 attempt 1: transient
            _make_response(_make_tagged_response_text(batch1)),  # batch 1 attempt 2: success
            _make_response(_make_tagged_response_text(batch2)),  # batch 2 attempt 1: success
        ],
    )
    agent: TaggingAgent = _make_agent(client, batch_size=2)

    result: list[TaggedChunk] = await agent.tag(chunks)

    assert len(result) == 4
    assert client.messages.create.await_count == 3


@pytest.mark.asyncio
async def test_tagging_agent_retries_on_malformed_json() -> None:
    """Malformed JSON in a batch is transient — retry then succeed."""
    chunks: list[dict[str, Any]] = [_make_chunk(0), _make_chunk(1)]
    client: MagicMock = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=[
            _make_response("not json"),
            _make_response(_make_tagged_response_text(chunks)),
        ],
    )
    agent: TaggingAgent = _make_agent(client, batch_size=15)

    result: list[TaggedChunk] = await agent.tag(chunks)

    assert len(result) == 2
    assert client.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_tagging_agent_does_not_retry_on_validation_error() -> None:
    """A pydantic ValidationError on a batch item is terminal — no retry."""
    chunks: list[dict[str, Any]] = [_make_chunk(0)]
    # Valid JSON but the item is missing ``chunk_index`` — TaggedChunk
    # validation will fail.
    bad_payload: str = json.dumps(
        [
            {
                # "chunk_index" deliberately omitted
                "page": 1,
                "is_heading": False,
                "text": "sample text",
                "relevant": True,
                "tags": ["authentication"],
                "reason": None,
            }
        ]
    )
    client: MagicMock = MagicMock()
    client.messages.create = AsyncMock(return_value=_make_response(bad_payload))
    agent: TaggingAgent = _make_agent(client, batch_size=15)

    with pytest.raises(ValidationError):
        await agent.tag(chunks)

    assert client.messages.create.await_count == 1
