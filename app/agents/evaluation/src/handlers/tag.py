"""Stage 4 -- Tag Lambda handler.

Triggered by EventBridge ``DocumentParsed``.  Resolves parsed chunks from the
event's payload envelope (inline or S3), tags them with security/governance
taxonomy labels via the LLM, and publishes a ``DocumentTagged`` event with the
tagged chunks carried in another payload envelope.  No Redis state is kept.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import anthropic
import boto3

from src.agents.schemas import DocumentParsedDetail, DocumentTaggedDetail, TaggedChunk
from src.agents.tagging_agent import TaggingAgent
from src.config import CloudWatchConfig, EventBridgeConfig, TaggingAgentConfig
from src.utils.eventbridge import EventBridgePublisher
from src.utils.payload_offload import inline_or_s3, resolve_payload

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Module-level singletons (cold-start reuse)
# ---------------------------------------------------------------------------

_publisher: EventBridgePublisher | None = None
_s3: Any = None
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
        # ``model`` is populated from yaml / env via BaseSettings sources.
        _tagging_config = TaggingAgentConfig()  # type: ignore[call-arg]
    return _tagging_config


def _get_publisher() -> EventBridgePublisher:
    """Return the module-level EventBridgePublisher singleton, creating on first call."""
    global _publisher  # noqa: PLW0603
    if _publisher is None:
        _publisher = EventBridgePublisher(EventBridgeConfig())
    return _publisher


def _get_s3() -> Any:
    """Return the module-level S3 client singleton, creating on first call."""
    global _s3  # noqa: PLW0603
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _get_cw() -> Any:
    """Return the module-level CloudWatch client singleton, creating on first call."""
    global _cw  # noqa: PLW0603
    if _cw is None:
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
        2. Resolve chunks from the inline-or-S3 payload envelope.
        3. Run the tagging agent against the chunks.
        4. Build the tagged-chunks payload envelope (inline or S3).
        5. Publish ``DocumentTagged`` event.
        6. Emit CloudWatch metrics.

    Args:
        event: Raw EventBridge Lambda event dict.
        context: Lambda context object (unused).

    Returns:
        Dict with ``statusCode`` 200 on success.
    """
    start: float = time.monotonic()

    # 1. Validate EventBridge event
    detail: DocumentParsedDetail = DocumentParsedDetail.model_validate(event["detail"])
    doc_id: str = detail.docId

    logger.info("Stage 4 Tag: doc_id=%s", doc_id)

    bucket: str = os.environ["S3_BUCKET"]
    s3_client: Any = _get_s3()

    # 2. Resolve chunks payload envelope
    chunks_bytes: bytes = resolve_payload(
        envelope=detail.payload.model_dump(),
        s3_client=s3_client,
        bucket=bucket,
    )
    chunks: list[dict[str, Any]] = json.loads(chunks_bytes)

    # 3. Run TaggingAgent
    client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic()
    agent: TaggingAgent = TaggingAgent(client=client, config=_get_tagging_config())
    tagged_chunks: list[TaggedChunk] = await agent.tag(chunks)

    # 4. Build payload envelope for the tagged output
    serialised: list[dict[str, Any]] = [tc.model_dump() for tc in tagged_chunks]
    envelope: dict[str, Any] = inline_or_s3(
        payload=serialised,
        doc_id=doc_id,
        stage="tagged",
        s3_client=s3_client,
        bucket=bucket,
    )

    # 5. Publish DocumentTagged event
    tagged_detail: DocumentTaggedDetail = DocumentTaggedDetail.model_validate(
        {"docId": doc_id, "payload": envelope}
    )
    await _get_publisher().publish("DocumentTagged", tagged_detail.model_dump(by_alias=True))

    # 6. Emit metrics
    duration_ms: float = (time.monotonic() - start) * 1000
    await _emit_metric("TaggingDuration", duration_ms)
    await _emit_metric("TaggedChunkCount", float(len(tagged_chunks)), unit="Count")

    logger.info(
        "Stage 4 complete: doc_id=%s tagged=%d duration_ms=%.1f",
        doc_id,
        len(tagged_chunks),
        duration_ms,
    )
    return {"statusCode": 200}
