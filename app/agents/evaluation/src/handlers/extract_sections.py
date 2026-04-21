"""Stage 5 -- Extract Sections Lambda handler.

Triggered by EventBridge ``DocumentTagged``.  Reads tagged chunks from Redis,
splits them into 5 agent-specific payloads, fetches agent-specific questions
from the database (cached in Redis), and enqueues 5 SQS Tasks messages --
one per specialist agent.  Large payloads are offloaded to S3.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from src.agents.schemas import DocumentTaggedDetail
from src.config import CloudWatchConfig, DatabaseConfig, PipelineConfig, RedisConfig
from src.db.questions_repo import fetch_questions_by_category
from src.utils.redis_client import (
    get_cache_config,
    get_redis,
    key_questions,
    redis_get_json,
    redis_set_json,
)

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Module-level singletons (cold-start reuse)
# ---------------------------------------------------------------------------

_redis_config: RedisConfig | None = None
_sqs: Any = None
_s3: Any = None
_cw: Any = None
_db_config: DatabaseConfig | None = None
_cw_config: CloudWatchConfig | None = None
_pipeline_config: PipelineConfig | None = None
_agent_tag_map_cache: dict[str, frozenset[str]] | None = None


def _get_redis_config() -> RedisConfig:
    """Return the module-level RedisConfig singleton, creating on first call."""
    global _redis_config  # noqa: PLW0603
    if _redis_config is None:
        _redis_config = RedisConfig()  # type: ignore[call-arg]
    return _redis_config


def _get_cw_config() -> CloudWatchConfig:
    """Return the module-level CloudWatchConfig singleton, creating on first call."""
    global _cw_config  # noqa: PLW0603
    if _cw_config is None:
        _cw_config = CloudWatchConfig()
    return _cw_config


def _get_pipeline_config() -> PipelineConfig:
    """Return the module-level PipelineConfig singleton, creating on first call."""
    global _pipeline_config  # noqa: PLW0603
    if _pipeline_config is None:
        _pipeline_config = PipelineConfig()
    return _pipeline_config


def _get_agent_tag_map() -> dict[str, frozenset[str]]:
    """Return the agent tag map with ``list[str]`` values converted to ``frozenset[str]``.

    Conversion is cached so it runs at most once per cold start.
    """
    global _agent_tag_map_cache  # noqa: PLW0603
    if _agent_tag_map_cache is None:
        raw: dict[str, list[str]] = _get_pipeline_config().agent_tag_map
        _agent_tag_map_cache = {agent: frozenset(tags) for agent, tags in raw.items()}
    return _agent_tag_map_cache


def _get_sqs() -> Any:
    """Return the module-level SQS client singleton, creating on first call."""
    global _sqs  # noqa: PLW0603
    if _sqs is None:
        import boto3

        _sqs = boto3.client("sqs")
    return _sqs


def _get_s3() -> Any:
    """Return the module-level S3 client singleton, creating on first call."""
    global _s3  # noqa: PLW0603
    if _s3 is None:
        import boto3

        _s3 = boto3.client("s3")
    return _s3


def _get_cw() -> Any:
    """Return the module-level CloudWatch client singleton, creating on first call."""
    global _cw  # noqa: PLW0603
    if _cw is None:
        import boto3

        _cw = boto3.client("cloudwatch")
    return _cw


def _get_db_config() -> DatabaseConfig:
    """Return the module-level DatabaseConfig singleton, creating on first call."""
    global _db_config  # noqa: PLW0603
    if _db_config is None:
        _db_config = DatabaseConfig()  # type: ignore[call-arg]
    return _db_config


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def extract_sections_for_agent(
    tagged_chunks: list[dict[str, Any]],
    agent_type: str,
) -> list[dict[str, Any]]:
    """Filter tagged chunks for a specific agent type.

    Includes:
    - Chunks where ``relevant=True`` and at least one tag matches the agent's set.
    - The nearest preceding heading chunk (``is_heading=True``), even if not
      itself tagged for this agent -- provides section context.

    Args:
        tagged_chunks: List of tagged chunk dicts from the tagging stage.
        agent_type: One of the 5 specialist agent types.

    Returns:
        Filtered list preserving original chunk order.
    """
    allowed_tags: frozenset[str] = _get_agent_tag_map()[agent_type]
    result: list[dict[str, Any]] = []
    last_heading: dict[str, Any] | None = None
    heading_added: bool = False

    for chunk in tagged_chunks:
        if chunk.get("is_heading"):
            last_heading = chunk
            heading_added = False

        tags: set[str] = set(chunk.get("tags", []))
        is_relevant: bool = chunk.get("relevant", False) and bool(tags & allowed_tags)

        if is_relevant:
            if last_heading and not heading_added:
                result.append(last_heading)
                heading_added = True
            result.append(chunk)

    return result


def _sections_to_text(sections: list[dict[str, Any]]) -> str:
    """Serialise sections to plain text for SQS message.

    Headings are prefixed with ``## ``, body chunks separated by double newlines.

    Args:
        sections: List of section chunk dicts.

    Returns:
        Plain-text representation of the sections.
    """
    lines: list[str] = []
    for chunk in sections:
        if chunk.get("is_heading"):
            lines.append(f"## {chunk['text']}")
        else:
            lines.append(chunk["text"])
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _load_questions(
    redis: Any,
    agent_type: str,
    dsn: str,
) -> list[dict[str, Any]]:
    """Load checklist questions with Redis cache-aside.

    On a cache miss, fetches from PostgreSQL via ``fetch_questions_by_category``
    and writes back to Redis.

    Args:
        redis: An ``aioredis.Redis`` instance.
        agent_type: The agent type to fetch questions for.
        dsn: asyncpg-compatible connection string.

    Returns:
        List of dicts with ``id`` and ``question`` fields.
    """
    cache_key: str = key_questions(agent_type)
    cached: Any = await redis_get_json(redis, cache_key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    questions: list[str] = await fetch_questions_by_category(dsn, agent_type)
    question_dicts: list[dict[str, Any]] = [
        {"id": i + 1, "question": q} for i, q in enumerate(questions)
    ]
    await redis_set_json(redis, cache_key, question_dicts, get_cache_config().ttl_questions)
    logger.info("Cached %d questions for agent_type=%s", len(question_dicts), agent_type)
    return question_dicts


async def _emit_metric(
    name: str,
    value: float,
    unit: str = "Count",
    agent_type: str | None = None,
) -> None:
    """Emit a CloudWatch metric via ``run_in_executor``.

    Args:
        name: Metric name (e.g. ``"SectionCount"``).
        value: Metric value.
        unit: CloudWatch unit string.
        agent_type: Optional dimension value for agent type.
    """
    dimensions: list[dict[str, str]] = []
    if agent_type is not None:
        dimensions.append({"Name": "AgentType", "Value": agent_type})

    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: _get_cw().put_metric_data(
            Namespace=_get_cw_config().namespace,
            MetricData=[
                {
                    "MetricName": name,
                    "Value": value,
                    "Unit": unit,
                    "Dimensions": dimensions,
                }
            ],
        ),
    )


async def _upload_payload_to_s3(
    s3_client: Any,
    bucket: str,
    s3_key: str,
    body: bytes,
) -> None:
    """Upload a large payload to S3 via ``run_in_executor``.

    Args:
        s3_client: A boto3 S3 client.
        bucket: S3 bucket name.
        s3_key: Object key in the bucket.
        body: Raw payload bytes.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: s3_client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=body,
            ContentType="application/json",
        ),
    )


async def _send_sqs_message(
    sqs_client: Any,
    queue_url: str,
    message_body: str,
) -> None:
    """Send a single SQS message via ``run_in_executor``.

    Args:
        sqs_client: A boto3 SQS client.
        queue_url: SQS queue URL.
        message_body: JSON string to send as the message body.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=message_body,
        ),
    )


async def _enqueue_sqs_message(
    payload: dict[str, Any],
    queue_url: str,
    sqs_client: Any,
    s3_client: Any,
    bucket: str,
) -> None:
    """Enqueue a single SQS message, offloading to S3 if too large.

    Args:
        payload: Full payload dict containing docId, agentType, document, etc.
        queue_url: SQS queue URL.
        sqs_client: A boto3 SQS client.
        s3_client: A boto3 S3 client (for large payload offload).
        bucket: S3 bucket name for payload storage.
    """
    message_body: str = json.dumps(payload)
    doc_id: str = payload["docId"]
    agent_type: str = payload["agentType"]

    if len(message_body.encode("utf-8")) <= _get_pipeline_config().sqs_inline_limit:
        await _send_sqs_message(sqs_client, queue_url, message_body)
    else:
        # Offload to S3
        s3_key: str = f"payloads/{doc_id}/{agent_type}.json"
        await _upload_payload_to_s3(s3_client, bucket, s3_key, message_body.encode("utf-8"))

        # Send SQS message with S3 pointer instead of document
        pointer_payload: dict[str, Any] = {
            "docId": doc_id,
            "agentType": agent_type,
            "s3PayloadKey": s3_key,
            "questions": payload["questions"],
            "enqueuedAt": payload["enqueuedAt"],
        }
        await _send_sqs_message(sqs_client, queue_url, json.dumps(pointer_payload))
        logger.info(
            "Large payload offloaded to S3: key=%s agent_type=%s doc_id=%s",
            s3_key,
            agent_type,
            doc_id,
        )


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point -- delegates to async core."""
    return asyncio.run(_handler(event, context))


async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Async core of the Stage 5 Extract Sections handler.

    Flow:
        1. Validate EventBridge event via Pydantic.
        2. Load tagged chunks from Redis.
        3. For each of 5 agent types, extract sections and fetch questions.
        4. Prepare all 5 payloads (fail-fast before any enqueue).
        5. Enqueue all 5 SQS messages.
        6. Emit ``SectionCount`` CloudWatch metric per agent type.

    Args:
        event: Raw EventBridge Lambda event dict.
        context: Lambda context object (unused).

    Returns:
        Dict with ``statusCode`` 200 on success.

    Raises:
        RuntimeError: If tagged chunks cache key is missing from Redis.
    """
    start: float = time.monotonic()

    # 1. Validate EventBridge event
    detail: DocumentTaggedDetail = DocumentTaggedDetail.model_validate(event["detail"])
    doc_id: str = detail.docId
    tagged_cache_key: str = detail.taggedCacheKey

    logger.info("Stage 5 ExtractSections: doc_id=%s tagged_key=%s", doc_id, tagged_cache_key)

    # 2. Get Redis connection and load tagged chunks
    redis = await get_redis(_get_redis_config())
    tagged_chunks: Any = await redis_get_json(redis, tagged_cache_key)

    if tagged_chunks is None:
        raise RuntimeError(
            f"Tagged chunks cache miss: key={tagged_cache_key} doc_id={doc_id}. "
            "Stage 4 may not have completed."
        )

    # 3. Prepare all 5 payloads before enqueueing any
    dsn: str = _get_db_config().dsn
    enqueued_at: str = datetime.now(tz=UTC).isoformat()
    payloads: list[dict[str, Any]] = []
    section_counts: list[tuple[str, int]] = []

    for agent_type in _get_pipeline_config().agent_types:
        sections: list[dict[str, Any]] = extract_sections_for_agent(tagged_chunks, agent_type)
        questions: list[dict[str, Any]] = await _load_questions(redis, agent_type, dsn)
        document_text: str = _sections_to_text(sections)

        payload: dict[str, Any] = {
            "docId": doc_id,
            "agentType": agent_type,
            "document": document_text,
            "questions": questions,
            "enqueuedAt": enqueued_at,
        }
        payloads.append(payload)
        section_counts.append((agent_type, len(sections)))

    # 4. Enqueue all 5 SQS messages
    queue_url: str = os.environ["SQS_TASKS_QUEUE_URL"]
    bucket: str = os.environ.get("S3_BUCKET", "")
    sqs_client: Any = _get_sqs()
    s3_client: Any = _get_s3()

    for payload in payloads:
        await _enqueue_sqs_message(
            payload=payload,
            queue_url=queue_url,
            sqs_client=sqs_client,
            s3_client=s3_client,
            bucket=bucket,
        )

    # 5. Emit SectionCount metrics
    for agent_type, count in section_counts:
        await _emit_metric("SectionCount", float(count), agent_type=agent_type)

    duration_ms: float = (time.monotonic() - start) * 1000
    logger.info(
        "Stage 5 complete: doc_id=%s duration_ms=%.1f sections=%s",
        doc_id,
        duration_ms,
        {at: c for at, c in section_counts},
    )
    return {"statusCode": 200}
