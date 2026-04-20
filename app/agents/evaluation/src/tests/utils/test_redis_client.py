"""Tests for src/utils/redis_client.py."""

import fakeredis.aioredis as fakeredis_aio
import pytest

from src.utils.redis_client import (
    get_cache_config,
    key_chunks,
    key_compiled,
    key_questions,
    key_receipt,
    key_result,
    key_results_count,
    key_sections,
    key_stage8_count,
    key_tagged,
    redis_delete_many,
    redis_get_json,
    redis_incr,
    redis_set_json,
)

# ---------------------------------------------------------------------------
# TTL constants
# ---------------------------------------------------------------------------


def test_cache_config_ttls_are_positive_ints() -> None:
    """All cache TTL values must be positive integers."""
    cfg = get_cache_config()
    for ttl in (
        cfg.ttl_chunks,
        cfg.ttl_tagged,
        cfg.ttl_sections,
        cfg.ttl_questions,
        cfg.ttl_result,
        cfg.ttl_results_count,
        cfg.ttl_compiled,
        cfg.ttl_stage8_count,
        cfg.ttl_receipt,
    ):
        assert isinstance(ttl, int)
        assert ttl > 0


# ---------------------------------------------------------------------------
# Key-naming helpers
# ---------------------------------------------------------------------------


def test_key_chunks() -> None:
    """key_chunks should return 'chunks:{hash}' format."""
    assert key_chunks("abc123") == "chunks:abc123"


def test_key_tagged() -> None:
    """key_tagged should return 'tagged:{hash}' format."""
    assert key_tagged("abc123") == "tagged:abc123"


def test_key_sections() -> None:
    """key_sections should return 'sections:{docId}:{agentType}' format."""
    assert key_sections("doc1", "security") == "sections:doc1:security"


def test_key_questions() -> None:
    """key_questions should return 'questions:{agentType}' format."""
    assert key_questions("risk") == "questions:risk"


def test_key_result() -> None:
    """key_result should return 'result:{docId}:{agentType}' format."""
    assert key_result("doc1", "ea") == "result:doc1:ea"


def test_key_results_count() -> None:
    """key_results_count should return 'results_count:{docId}' format."""
    assert key_results_count("doc1") == "results_count:doc1"


def test_key_compiled() -> None:
    """key_compiled should return 'compiled:{docId}' format."""
    assert key_compiled("doc1") == "compiled:doc1"


def test_key_stage8_count() -> None:
    """key_stage8_count should return 'stage8_count:{docId}' format."""
    assert key_stage8_count("doc1") == "stage8_count:doc1"


def test_key_receipt() -> None:
    """key_receipt should return 'receipt:{docId}' format."""
    assert key_receipt("doc1") == "receipt:doc1"


# ---------------------------------------------------------------------------
# JSON wrappers
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_client() -> fakeredis_aio.FakeRedis:
    """Provide an in-memory fake Redis for testing."""
    return fakeredis_aio.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_redis_set_and_get_json(redis_client: fakeredis_aio.FakeRedis) -> None:
    """Set a JSON value and read it back."""
    data = {"foo": "bar", "nums": [1, 2, 3]}
    await redis_set_json(redis_client, "test:key", data, ttl=60)
    result = await redis_get_json(redis_client, "test:key")
    assert result == data


@pytest.mark.asyncio
async def test_redis_get_json_miss(redis_client: fakeredis_aio.FakeRedis) -> None:
    """A cache miss should return None."""
    result = await redis_get_json(redis_client, "nonexistent:key")
    assert result is None


@pytest.mark.asyncio
async def test_redis_set_json_with_ttl(redis_client: fakeredis_aio.FakeRedis) -> None:
    """The key should have a TTL after being set."""
    await redis_set_json(redis_client, "ttl:key", {"x": 1}, ttl=300)
    ttl_remaining: int = await redis_client.ttl("ttl:key")
    assert 0 < ttl_remaining <= 300


# ---------------------------------------------------------------------------
# INCR with first-write-only TTL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_incr_first_call(redis_client: fakeredis_aio.FakeRedis) -> None:
    """First INCR should return 1 and set TTL."""
    result = await redis_incr(redis_client, "counter:doc1", ttl=120)
    assert result == 1
    ttl_remaining: int = await redis_client.ttl("counter:doc1")
    assert 0 < ttl_remaining <= 120


@pytest.mark.asyncio
async def test_redis_incr_subsequent_calls(redis_client: fakeredis_aio.FakeRedis) -> None:
    """Subsequent INCRs should increment without resetting TTL."""
    await redis_incr(redis_client, "counter:doc2", ttl=120)
    result = await redis_incr(redis_client, "counter:doc2", ttl=120)
    assert result == 2


# ---------------------------------------------------------------------------
# Bulk delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_delete_many(redis_client: fakeredis_aio.FakeRedis) -> None:
    """delete_many should remove all specified keys."""
    await redis_client.set("a", "1")
    await redis_client.set("b", "2")
    await redis_client.set("c", "3")

    await redis_delete_many(redis_client, "a", "b")

    assert await redis_client.get("a") is None
    assert await redis_client.get("b") is None
    assert await redis_client.get("c") == "3"
