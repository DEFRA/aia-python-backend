"""Stage 4 -- Tag Lambda handler.

Triggered by EventBridge (DocumentParsed). Tags document chunks with
security/governance taxonomy labels and publishes a ``DocumentTagged`` event.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import anthropic

from src.agents.schemas import DocumentParsedDetail, DocumentTaggedDetail, TaggedChunk
from src.agents.tagging_agent import TaggingAgent
from src.config import CloudWatchConfig, EventBridgeConfig, RedisConfig, TaggingAgentConfig
from src.utils.eventbridge import EventBridgePublisher
from src.utils.redis_client import (
    get_cache_config,
    get_redis,
    key_chunks,
    key_tagged,
    redis_get_json,
    redis_set_json,
)

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Module-level singletons (cold-start reuse)
# ---------------------------------------------------------------------------

_redis_config: RedisConfig | None = None
_publisher: EventBridgePublisher | None = None
_cw: Any = None
_cw_config: CloudWatchConfig | None = None
_tagging_config: TaggingAgentConfig | None = None


def _get_cw_config() -> CloudWatchConfig:
    """Return the module-level CloudWatchConfig singleton, creating on first call."""
    global _cw_config  # noqa: PLW0603
    if _cw_config is None:
        _cw_config = CloudWatchConfig()
    return _cw_config


def _get_tagging_config() -> TaggingAgentConfig:
    """Return the module-level TaggingAgentConfig singleton, creating on first call."""
    global _tagging_config  # noqa: PLW0603
    if _tagging_config is None:
        _tagging_config = TaggingAgentConfig()
    return _tagging_config


def _get_redis_config() -> RedisConfig:
    """Return the module-level RedisConfig singleton, creating on first call."""
    global _redis_config  # noqa: PLW0603
    if _redis_config is None:
        _redis_config = RedisConfig()  # type: ignore[call-arg]
    return _redis_config


def _get_publisher() -> EventBridgePublisher:
    """Return the module-level EventBridgePublisher singleton, creating on first call."""
    global _publisher  # noqa: PLW0603
    if _publisher is None:
        _publisher = EventBridgePublisher(EventBridgeConfig())
    return _publisher


def _get_cw() -> Any:
    """Return the module-level CloudWatch client singleton, creating on first call."""
    global _cw  # noqa: PLW0603
    if _cw is None:
        import boto3

        _cw = boto3.client("cloudwatch")
    return _cw


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _emit_metric(name: str, value: float, unit: str = "Milliseconds") -> None:
    """Emit a CloudWatch metric via ``run_in_executor``.

    Args:
        name: Metric name (e.g. ``"TaggingDuration"``).
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


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point -- delegates to async core."""
    return asyncio.run(_handler(event, context))


async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Async core of the Stage 4 Tag handler.

    Flow:
        1. Validate EventBridge event via Pydantic.
        2. Cache check -- if ``tagged:{content_hash}`` exists, skip Claude.
        3. On cache miss:
           a. Load chunks from Redis.
           b. Create Anthropic client and run TaggingAgent.
           c. Cache tagged output with TTL_TAGGED.
        4. Publish ``DocumentTagged`` event.
        5. Emit CloudWatch metrics (non-cached runs only).

    Args:
        event: Raw EventBridge Lambda event dict.
        context: Lambda context object (unused).

    Returns:
        Dict with ``statusCode`` 200 on success.

    Raises:
        RuntimeError: If chunks cache key is missing from Redis.
    """
    start: float = time.monotonic()

    # 1. Validate EventBridge event
    detail: DocumentParsedDetail = DocumentParsedDetail.model_validate(event["detail"])
    doc_id: str = detail.docId
    content_hash: str = detail.contentHash

    logger.info("Stage 4 Tag: doc_id=%s content_hash=%s", doc_id, content_hash)

    # 2. Get Redis connection
    redis = await get_redis(_get_redis_config())

    # 3. Cache check
    tagged_cache_key: str = key_tagged(content_hash)
    cached: Any = await redis_get_json(redis, tagged_cache_key)

    if cached is not None:
        logger.info("Cache hit for tagged output: key=%s doc_id=%s", tagged_cache_key, doc_id)
    else:
        # 4a. Load chunks from Redis
        chunks_cache_key: str = key_chunks(content_hash)
        chunks: list[dict[str, Any]] | None = await redis_get_json(redis, chunks_cache_key)
        if chunks is None:
            raise RuntimeError(
                f"Chunks cache miss: key={chunks_cache_key} doc_id={doc_id}. "
                "Stage 3 may not have completed."
            )

        # 4b. Run TaggingAgent
        client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic()
        agent: TaggingAgent = TaggingAgent(client=client, config=_get_tagging_config())
        tagged_chunks: list[TaggedChunk] = await agent.tag(chunks)

        # 4c. Cache tagged output
        serialised: list[dict[str, Any]] = [tc.model_dump() for tc in tagged_chunks]
        await redis_set_json(redis, tagged_cache_key, serialised, get_cache_config().ttl_tagged)
        logger.info(
            "Cached %d tagged chunks: key=%s doc_id=%s",
            len(tagged_chunks),
            tagged_cache_key,
            doc_id,
        )

        # 5. Emit metrics (non-cached path only)
        duration_ms: float = (time.monotonic() - start) * 1000
        await _emit_metric("TaggingDuration", duration_ms)
        await _emit_metric("TaggedChunkCount", float(len(tagged_chunks)), unit="Count")

    # 6. Publish DocumentTagged event
    tagged_detail: DocumentTaggedDetail = DocumentTaggedDetail(
        docId=doc_id,
        taggedCacheKey=tagged_cache_key,
        contentHash=content_hash,
    )
    await _get_publisher().publish("DocumentTagged", tagged_detail.model_dump(by_alias=True))

    logger.info("Stage 4 complete: doc_id=%s", doc_id)
    return {"statusCode": 200}
