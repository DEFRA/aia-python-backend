"""Stage 6 -- Specialist Agent Lambda handler (SQS-triggered dispatcher).

Triggered by SQS Tasks queue (batch size = 1). Resolves the agent type from
the message body, runs the specialist agent, and publishes the result to the
SQS Status queue. Catches agent exceptions and publishes failure status rather
than letting them propagate -- the downstream compile stage needs visibility
of both successes and failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import anthropic
import boto3
from pydantic import BaseModel

from src.agents.data_agent import DataAgent
from src.agents.ea_agent import EAAgent
from src.agents.risk_agent import RiskAgent
from src.agents.schemas import AgentResult
from src.agents.security_agent import SecurityAgent
from src.agents.solution_agent import SolutionAgent
from src.config import (
    CloudWatchConfig,
    DataAgentConfig,
    EAAgentConfig,
    RiskAgentConfig,
    SecurityAgentConfig,
    SolutionAgentConfig,
)

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Typed agent / config protocols
# ---------------------------------------------------------------------------


class SpecialistAgentConfig(Protocol):
    """Structural type for any specialist agent's Pydantic config.

    All five specialist agent configs expose ``api_key``, ``model``,
    ``max_tokens`` and ``temperature``; declaring them here removes the need
    for ``Any`` annotations at the dispatch site.
    """

    api_key: str
    model: str
    max_tokens: int
    temperature: float


class SpecialistAgent(Protocol):
    """Structural type for a specialist agent with an async ``assess``."""

    async def assess(self, document: str, questions: list[str]) -> AgentResult: ...


# Typed factories for the dispatch registries. ``Callable[..., T]`` accepts
# any keyword signature (the concrete agents use ``client=`` + ``agent_config=``)
# while preserving a typed return — so ``AGENT_REGISTRY[agent_type](...)`` is
# known to produce a ``SpecialistAgent`` without resorting to ``Any``.
SpecialistAgentFactory = Callable[..., SpecialistAgent]
SpecialistConfigFactory = Callable[..., SpecialistAgentConfig]


# ---------------------------------------------------------------------------
# Agent and config registries
# ---------------------------------------------------------------------------

AGENT_REGISTRY: dict[str, SpecialistAgentFactory] = {
    "security": SecurityAgent,
    "data": DataAgent,
    "risk": RiskAgent,
    "ea": EAAgent,
    "solution": SolutionAgent,
}

CONFIG_REGISTRY: dict[str, SpecialistConfigFactory] = {
    "security": SecurityAgentConfig,
    "data": DataAgentConfig,
    "risk": RiskAgentConfig,
    "ea": EAAgentConfig,
    "solution": SolutionAgentConfig,
}

# ---------------------------------------------------------------------------
# SQS event Pydantic models
# ---------------------------------------------------------------------------


class AgentTaskBody(BaseModel):
    """JSON body inside the SQS Tasks queue message."""

    docId: str
    agentType: str
    document: str | None = None
    s3PayloadKey: str | None = None
    questions: list[dict[str, Any]]
    enqueuedAt: str


class AgentSqsRecord(BaseModel):
    """A single SQS record from the Lambda event."""

    body: str


class AgentSqsEvent(BaseModel):
    """Top-level SQS event envelope for the agent handler."""

    Records: list[AgentSqsRecord]


# ---------------------------------------------------------------------------
# Module-level singletons (cold-start reuse)
# ---------------------------------------------------------------------------

_sqs: Any = None
_s3: Any = None
_cw: Any = None
_cw_config: CloudWatchConfig | None = None


def _get_cw_config() -> CloudWatchConfig:
    """Return the module-level CloudWatchConfig singleton, creating on first call."""
    global _cw_config  # noqa: PLW0603
    if _cw_config is None:
        _cw_config = CloudWatchConfig()
    return _cw_config


def _get_sqs() -> Any:
    """Return the module-level SQS client singleton, creating on first call."""
    global _sqs  # noqa: PLW0603
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


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


async def _download_s3_payload(s3_client: Any, bucket: str, key: str) -> str:
    """Download a text payload from S3 via ``run_in_executor``.

    Args:
        s3_client: A boto3 S3 client.
        bucket: S3 bucket name.
        key: S3 object key.

    Returns:
        The decoded UTF-8 text content of the S3 object.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    response: dict[str, Any] = await loop.run_in_executor(
        None,
        lambda: s3_client.get_object(Bucket=bucket, Key=key),
    )
    body_bytes: bytes = await loop.run_in_executor(None, response["Body"].read)
    return body_bytes.decode("utf-8")


async def _send_status_message(
    sqs_client: Any,
    queue_url: str,
    message_body: dict[str, Any],
) -> None:
    """Send a message to the SQS Status queue via ``run_in_executor``.

    Args:
        sqs_client: A boto3 SQS client.
        queue_url: URL of the SQS Status queue.
        message_body: JSON-serialisable dict to send as the message body.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message_body),
        ),
    )


async def _emit_metric(
    name: str,
    value: float,
    unit: str = "Milliseconds",
    agent_type: str = "",
) -> None:
    """Emit a CloudWatch metric with agentType dimension via ``run_in_executor``.

    Args:
        name: Metric name (e.g. ``"AgentDuration"``).
        value: Metric value.
        unit: CloudWatch unit string.
        agent_type: Agent type for the dimension.
    """
    dimensions: list[dict[str, str]] = []
    if agent_type:
        dimensions.append({"Name": "agentType", "Value": agent_type})

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


def _extract_question_strings(questions: list[dict[str, Any]]) -> list[str]:
    """Extract plain question strings from the question dicts.

    Args:
        questions: List of dicts each containing a ``"question"`` key.

    Returns:
        Ordered list of question text strings.
    """
    return [q["question"] for q in questions]


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point -- delegates to async core."""
    return asyncio.run(_handler(event, context))


async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Async core of the Stage 6 Agent handler.

    Flow:
        1. Validate SQS event via Pydantic.
        2. Resolve document text (inline or from S3).
        3. Extract question strings from question dicts.
        4. Look up agent class and config from registries.
        5. Run agent assessment.
        6. On success: publish completed status to SQS Status queue.
        7. On failure: catch exception, publish failed status to SQS Status queue.
        8. Emit CloudWatch metrics.

    Args:
        event: Raw SQS Lambda event dict.
        context: Lambda context object (unused).

    Returns:
        Dict with ``statusCode`` 200 on success.
    """
    start: float = time.monotonic()

    # 1. Validate SQS event
    sqs_event: AgentSqsEvent = AgentSqsEvent.model_validate(event)
    record: AgentSqsRecord = sqs_event.Records[0]
    body: AgentTaskBody = AgentTaskBody.model_validate_json(record.body)
    doc_id: str = body.docId
    agent_type: str = body.agentType

    logger.info("Stage 6 Agent: doc_id=%s agent_type=%s", doc_id, agent_type)

    # 2. Resolve document text
    document: str
    if body.s3PayloadKey is not None:
        bucket: str = os.environ["S3_BUCKET"]
        document = await _download_s3_payload(_get_s3(), bucket, body.s3PayloadKey)
    elif body.document is not None:
        document = body.document
    else:
        raise ValueError(f"Neither document nor s3PayloadKey provided: doc_id={doc_id}")

    # 3. Extract question strings
    question_strings: list[str] = _extract_question_strings(body.questions)

    # 4. Look up agent class and config
    if agent_type not in AGENT_REGISTRY:
        raise ValueError(f"Unknown agent type: {agent_type}")

    agent_cls: SpecialistAgentFactory = AGENT_REGISTRY[agent_type]
    config_cls: SpecialistConfigFactory = CONFIG_REGISTRY[agent_type]
    agent_config: SpecialistAgentConfig = config_cls()

    client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic(
        api_key=agent_config.api_key,
    )
    agent: SpecialistAgent = agent_cls(client=client, agent_config=agent_config)

    # 5. Run assessment and publish result
    status_queue_url: str = os.environ["SQS_STATUS_QUEUE_URL"]
    duration_ms: float

    try:
        result: AgentResult = await agent.assess(document, question_strings)
        duration_ms = (time.monotonic() - start) * 1000
        completed_at: str = datetime.now(tz=UTC).isoformat()

        # 6. Publish success to SQS Status queue
        status_message: dict[str, Any] = {
            "docId": doc_id,
            "agentType": agent_type,
            "status": "completed",
            "result": result.model_dump(),
            "durationMs": round(duration_ms, 1),
            "completedAt": completed_at,
        }
        await _send_status_message(_get_sqs(), status_queue_url, status_message)

        # 8. Emit success metrics
        await _emit_metric("AgentDuration", duration_ms, "Milliseconds", agent_type)
        await _emit_metric("AgentSuccess", 1.0, "Count", agent_type)

        logger.info(
            "Stage 6 complete: doc_id=%s agent_type=%s duration_ms=%.1f",
            doc_id,
            agent_type,
            duration_ms,
        )

    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000

        # 7. Publish failure to SQS Status queue
        logger.error(
            "Agent assessment failed: doc_id=%s agent_type=%s error=%s",
            doc_id,
            agent_type,
            exc,
        )
        failure_message: dict[str, Any] = {
            "docId": doc_id,
            "agentType": agent_type,
            "status": "failed",
            "errorMessage": str(exc),
        }
        await _send_status_message(_get_sqs(), status_queue_url, failure_message)

        # 8. Emit failure metrics
        await _emit_metric("AgentDuration", duration_ms, "Milliseconds", agent_type)
        await _emit_metric("AgentFailure", 1.0, "Count", agent_type)

    return {"statusCode": 200}
