"""Stage 5 -- Extract Sections Lambda handler.

Triggered by EventBridge ``DocumentTagged``.  Resolves the tagged-chunks payload
envelope (inline or S3), splits the chunks into agent-specific sections,
fetches per-agent assessment questions, and enqueues one SQS Tasks message per
specialist agent.  Section text larger than the SQS inline limit is offloaded
to S3 in the same shape used by Stage 6.  No Redis state is kept.
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
from src.config import CloudWatchConfig, DatabaseConfig, PipelineConfig
from src.db.questions_repo import fetch_assessment_by_category
from src.handlers.agent import AgentTaskBody
from src.utils.payload_offload import resolve_payload

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Module-level singletons (cold-start reuse)
# ---------------------------------------------------------------------------

_sqs: Any = None
_s3: Any = None
_cw: Any = None
_cw_config: CloudWatchConfig | None = None
_pipeline_config: PipelineConfig | None = None
_db_config: DatabaseConfig | None = None
_agent_tag_map_cache: dict[str, frozenset[str]] | None = None


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


def _get_db_config() -> DatabaseConfig:
    """Return the module-level DatabaseConfig singleton, creating on first call."""
    global _db_config  # noqa: PLW0603
    if _db_config is None:
        _db_config = DatabaseConfig()
    return _db_config


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
        agent_type: One of the surviving specialist agent types.

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


async def _enqueue_sqs_message(  # noqa: PLR0913
    payload: dict[str, Any],
    queue_url: str,
    sqs_client: Any,
    s3_client: Any,
    bucket: str,
    inline_limit: int,
) -> None:
    """Enqueue a single SQS message, offloading the document text to S3 if too large.

    Args:
        payload: Full payload dict containing document_id, agentType, document, etc.
        queue_url: SQS queue URL.
        sqs_client: A boto3 SQS client.
        s3_client: A boto3 S3 client (for large payload offload).
        bucket: S3 bucket name for payload storage.
        inline_limit: Maximum inline body size in bytes.
    """
    message_body: str = json.dumps(payload)
    doc_id: str = payload["document_id"]
    agent_type: str = payload["agentType"]

    if len(message_body.encode("utf-8")) <= inline_limit:
        await _send_sqs_message(sqs_client, queue_url, message_body)
        return

    # Offload to S3 — the document text is what bloats the message
    s3_key: str = f"payloads/{doc_id}/{agent_type}.json"
    await _upload_payload_to_s3(s3_client, bucket, s3_key, message_body.encode("utf-8"))

    # Re-validate through ``AgentTaskBody`` so the pointer message is a typed
    # Pydantic-serialised payload (Pydantic Boundary Validation rule).
    pointer_body: AgentTaskBody = AgentTaskBody.model_validate(
        {
            "document_id": doc_id,
            "agentType": agent_type,
            "s3PayloadKey": s3_key,
            "questions": payload["questions"],
            "categoryUrl": payload["categoryUrl"],
            "enqueuedAt": payload["enqueuedAt"],
        }
    )
    await _send_sqs_message(sqs_client, queue_url, pointer_body.model_dump_json())
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
        2. Resolve tagged chunks from the inline-or-S3 payload envelope.
        3. For each agent type, extract sections and load questions.
        4. Build all SQS Tasks payloads (fail-fast before any enqueue).
        5. Enqueue all SQS Tasks messages (offloading section text to S3 if needed).
        6. Emit ``SectionCount`` CloudWatch metric per agent type.

    Args:
        event: Raw EventBridge Lambda event dict.
        context: Lambda context object (unused).

    Returns:
        Dict with ``statusCode`` 200 on success.
    """
    start: float = time.monotonic()

    # 1. Validate EventBridge event
    detail: DocumentTaggedDetail = DocumentTaggedDetail.model_validate(event["detail"])
    doc_id: str = detail.document_id

    logger.info("Stage 5 ExtractSections: doc_id=%s", doc_id)

    # 2. Resolve tagged-chunks payload envelope
    bucket: str = os.environ["S3_BUCKET"]
    s3_client: Any = _get_s3()
    tagged_bytes: bytes = resolve_payload(
        envelope=detail.payload.model_dump(),
        s3_client=s3_client,
        bucket=bucket,
    )
    tagged_chunks: list[dict[str, Any]] = json.loads(tagged_bytes)

    # 3. Build payloads for every agent type before enqueueing any
    enqueued_at: str = datetime.now(tz=UTC).isoformat()
    payloads: list[dict[str, Any]] = []
    section_counts: list[tuple[str, int]] = []

    for agent_type in _get_pipeline_config().agent_types:
        sections: list[dict[str, Any]] = extract_sections_for_agent(tagged_chunks, agent_type)
        questions, category_url = await fetch_assessment_by_category(
            _get_db_config().dsn, agent_type
        )
        document_text: str = _sections_to_text(sections)

        body: AgentTaskBody = AgentTaskBody(
            document_id=doc_id,
            agentType=agent_type,
            document=document_text,
            questions=questions,
            categoryUrl=category_url,
            enqueuedAt=enqueued_at,
        )
        payloads.append(body.model_dump(mode="json"))
        section_counts.append((agent_type, len(sections)))

    # 4. Enqueue all SQS messages
    queue_url: str = os.environ["SQS_TASKS_QUEUE_URL"]
    sqs_client: Any = _get_sqs()
    inline_limit: int = _get_pipeline_config().sqs_inline_limit

    for payload in payloads:
        await _enqueue_sqs_message(
            payload=payload,
            queue_url=queue_url,
            sqs_client=sqs_client,
            s3_client=s3_client,
            bucket=bucket,
            inline_limit=inline_limit,
        )

    # 5. Emit metrics
    for agent_type, count in section_counts:
        await _emit_metric("SectionCount", float(count), agent_type=agent_type)

    duration_ms: float = (time.monotonic() - start) * 1000
    logger.info(
        "Stage 5 complete: doc_id=%s duration_ms=%.1f sections=%s",
        doc_id,
        duration_ms,
        dict(section_counts),
    )
    return {"statusCode": 200}
