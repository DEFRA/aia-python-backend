"""Redis client utility — shared pipeline state store.

Provides connection pooling, typed JSON wrappers, TTL constants, and
key-naming helpers used by every Lambda handler in the pipeline.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from src.config import CacheConfig, RedisConfig

logger: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL configuration (seconds) — sourced from CacheConfig (config.yaml)
# ---------------------------------------------------------------------------

_cache_config: CacheConfig | None = None


def get_cache_config() -> CacheConfig:
    """Return the module-level ``CacheConfig`` singleton.

    Created once per cold start so handlers can import a single shared
    instance without each handler instantiating its own settings object.
    """
    global _cache_config  # noqa: PLW0603
    if _cache_config is None:
        _cache_config = CacheConfig()
    return _cache_config


# ---------------------------------------------------------------------------
# Module-level connection singleton
# ---------------------------------------------------------------------------

_pool: aioredis.Redis | None = None


async def get_redis(config: RedisConfig) -> aioredis.Redis:
    """Return a shared ``redis.asyncio.Redis`` connection pool.

    Creates the pool once per Lambda cold start.  Subsequent calls return the
    same instance.  Uses SSL/TLS by default for ElastiCache in-transit
    encryption (``ssl_cert_reqs="none"`` avoids cert pinning).

    Args:
        config: Redis connection settings.  Callers must create a
            ``RedisConfig`` instance at module level and pass it in.
    """
    global _pool  # noqa: PLW0603
    if _pool is not None:
        return _pool

    kwargs: dict[str, Any] = {
        "host": config.host,
        "port": config.port,
        "db": config.db,
        "socket_timeout": config.socket_timeout,
        "socket_connect_timeout": config.socket_connect_timeout,
        "decode_responses": True,
    }

    if config.ssl:
        kwargs["ssl"] = True
        kwargs["ssl_cert_reqs"] = "none"

    _pool = aioredis.Redis(**kwargs)
    logger.info("Redis pool created: host=%s port=%d ssl=%s", config.host, config.port, config.ssl)
    return _pool


# ---------------------------------------------------------------------------
# Typed JSON wrappers
# ---------------------------------------------------------------------------


async def redis_get_json(
    client: aioredis.Redis,
    key: str,
) -> Any:
    """Fetch a key from Redis and JSON-decode the value.

    Args:
        client: An ``aioredis.Redis`` instance.
        key: The Redis key to look up.

    Returns:
        The decoded Python object, or ``None`` on a cache miss.
    """
    raw: str | None = await client.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def redis_set_json(
    client: aioredis.Redis,
    key: str,
    value: Any,
    ttl: int,
) -> None:
    """JSON-encode *value* and store it in Redis with a TTL.

    Args:
        client: An ``aioredis.Redis`` instance.
        key: The Redis key.
        value: Any JSON-serialisable Python object.
        ttl: Time-to-live in seconds.
    """
    await client.setex(key, ttl, json.dumps(value))


async def redis_incr(
    client: aioredis.Redis,
    key: str,
    ttl: int,
) -> int:
    """Atomically increment a counter, setting TTL only on first write.

    Uses a pipeline to ``INCR`` then conditionally ``EXPIRE`` — the TTL is
    set **only** when the counter transitions from 0 to 1.  This prevents
    resetting the TTL on every agent completion, which would corrupt the
    fan-in counter window.

    Args:
        client: An ``aioredis.Redis`` instance.
        key: The counter key.
        ttl: Time-to-live in seconds (applied on first write only).

    Returns:
        The new counter value after incrementing.
    """
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.ttl(key)
    results: list[Any] = await pipe.execute()
    count: int = results[0]
    current_ttl: int = results[1]

    # Set TTL only on first write (count == 1) or if no TTL is set yet
    if count == 1 or current_ttl < 0:
        await client.expire(key, ttl)

    return count


async def redis_delete_many(
    client: aioredis.Redis,
    *keys: str,
) -> None:
    """Delete multiple keys in a single pipeline call.

    Args:
        client: An ``aioredis.Redis`` instance.
        keys: One or more Redis keys to delete.
    """
    if not keys:
        return
    pipe = client.pipeline()
    for k in keys:
        pipe.delete(k)
    await pipe.execute()


# ---------------------------------------------------------------------------
# Key-naming helpers
# ---------------------------------------------------------------------------


def key_chunks(content_hash: str) -> str:
    """Build the cache key for parsed document chunks."""
    return f"chunks:{content_hash}"


def key_tagged(content_hash: str) -> str:
    """Build the cache key for tagged document output."""
    return f"tagged:{content_hash}"


def key_sections(doc_id: str, agent_type: str) -> str:
    """Build the cache key for per-agent section slices."""
    return f"sections:{doc_id}:{agent_type}"


def key_questions(agent_type: str) -> str:
    """Build the cache key for checklist questions."""
    return f"questions:{agent_type}"


def key_result(doc_id: str, agent_type: str) -> str:
    """Build the cache key for an individual agent result."""
    return f"result:{doc_id}:{agent_type}"


def key_results_count(doc_id: str) -> str:
    """Build the counter key for Stage 7 fan-in."""
    return f"results_count:{doc_id}"


def key_compiled(doc_id: str) -> str:
    """Build the cache key for the compiled report payload."""
    return f"compiled:{doc_id}"


def key_stage8_count(doc_id: str) -> str:
    """Build the counter key for Stage 8 fan-in (Persist + Move)."""
    return f"stage8_count:{doc_id}"


def key_receipt(doc_id: str) -> str:
    """Build the cache key for the SQS receipt handle."""
    return f"receipt:{doc_id}"
