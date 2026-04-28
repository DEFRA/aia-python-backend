"""Stage 7 -- Compile Lambda handler (SQS Status queue fan-in).

Triggered by the SQS Status queue (batch size = 1).  Each message is an
``AgentStatusMessage`` from a specialist agent.  The handler:

1. Validates the SQS body via ``AgentStatusMessage``.
2. Writes the result (or a failure marker) to Redis under
   ``result:{docId}:{agentType}``.
3. Atomically increments the fan-in counter ``results_count:{docId}``.
4. When the counter reaches the number of specialist agents, reads all
   results back from Redis, renders a combined markdown report, stores
   the ``CompiledResult`` under ``compiled:{docId}``, and publishes a
   ``DocumentCompiled`` EventBridge event.

Fan-in concurrency is handled by Redis ``INCR``: the counter transitions
through each value at most once, so the equality check ``count == N``
triggers exactly one compile.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from src.agents.schemas import (
    AgentResult,
    AgentStatusMessage,
    AssessmentRow,
    CompiledContentBlock,
    CompiledResult,
    DocumentCompiledDetail,
    FinalSummary,
)
from src.config import EventBridgeConfig, PipelineConfig, RedisConfig
from src.utils.eventbridge import EventBridgePublisher
from src.utils.redis_client import (
    get_cache_config,
    get_redis,
    key_compiled,
    key_result,
    key_results_count,
    redis_get_json,
    redis_incr,
    redis_set_json,
)

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AGENT_DISPLAY_NAMES: dict[str, str] = {
    "security": "Security",
    "governance": "Information Governance",
}

_RATING_ICONS: dict[str, str] = {
    "Green": "🟢 Green",
    "Amber": "🟠 Amber",
    "Red": "🔴 Red",
}

_DEFAULT_DOC_TYPE: str = "Security Assessment"


# ---------------------------------------------------------------------------
# SQS event envelope models
# ---------------------------------------------------------------------------


class _CompileSqsRecord(BaseModel):
    """A single SQS record as delivered to the compile handler."""

    body: str


class _CompileSqsEvent(BaseModel):
    """Top-level SQS event envelope for the Stage 7 compile handler."""

    Records: list[_CompileSqsRecord] = Field(min_length=1)


class _AgentFailureMarker(BaseModel):
    """Typed Redis marker written when an agent failed rather than returned a result."""

    status: Literal["failed"]
    error: str


# ---------------------------------------------------------------------------
# Module-level singletons (cold-start reuse)
# ---------------------------------------------------------------------------

_redis_config: RedisConfig | None = None
_eb_config: EventBridgeConfig | None = None
_publisher: EventBridgePublisher | None = None
_pipeline_config: PipelineConfig | None = None


def _get_redis_config() -> RedisConfig:
    """Return the module-level RedisConfig singleton, creating on first call."""
    global _redis_config  # noqa: PLW0603
    if _redis_config is None:
        # BaseSettings reads required fields from env at construction time;
        # mypy cannot see the env-provided arguments so suppress call-arg here.
        _redis_config = RedisConfig()  # type: ignore[call-arg]
    return _redis_config


def _get_pipeline_config() -> PipelineConfig:
    """Return the module-level PipelineConfig singleton, creating on first call."""
    global _pipeline_config  # noqa: PLW0603
    if _pipeline_config is None:
        _pipeline_config = PipelineConfig()
    return _pipeline_config


def _get_eventbridge_config() -> EventBridgeConfig:
    """Return the module-level EventBridgeConfig singleton, creating on first call."""
    global _eb_config  # noqa: PLW0603
    if _eb_config is None:
        _eb_config = EventBridgeConfig()
    return _eb_config


def _get_publisher() -> EventBridgePublisher:
    """Return the module-level EventBridgePublisher singleton, creating on first call."""
    global _publisher  # noqa: PLW0603
    if _publisher is None:
        _publisher = EventBridgePublisher(config=_get_eventbridge_config())
    return _publisher


async def _get_redis() -> Any:
    """Return the shared aioredis connection, creating on first call."""
    return await get_redis(_get_redis_config())


# ---------------------------------------------------------------------------
# Markdown rendering helpers
# ---------------------------------------------------------------------------


def _sanitise_cell(value: str) -> str:
    """Normalise whitespace and escape pipes so a value fits in a markdown table cell."""
    collapsed: str = " ".join(value.split())
    return collapsed.replace("|", "\\|")


def _render_reference_cell(row: AssessmentRow) -> str:
    """Render the Reference cell as ``[text](url)`` when a URL is available."""
    text: str = _sanitise_cell(row.Reference.text)
    if row.Reference.url:
        return f"[{text}]({row.Reference.url})"
    return text


def _render_row(row: AssessmentRow) -> str:
    """Render a single AssessmentRow as a markdown table row."""
    rating: str = _RATING_ICONS.get(row.Rating, row.Rating)
    question: str = _sanitise_cell(row.Question)
    comments: str = _sanitise_cell(row.Comments)
    reference: str = _render_reference_cell(row)
    return f"| {question} | {rating} | {comments} | {reference} |"


def _count_by_rating(rows: list[AssessmentRow]) -> tuple[int, int, int]:
    """Return ``(red, amber, green)`` counts for a list of assessment rows."""
    red: int = sum(1 for r in rows if r.Rating == "Red")
    amber: int = sum(1 for r in rows if r.Rating == "Amber")
    green: int = sum(1 for r in rows if r.Rating == "Green")
    return red, amber, green


def _render_summary_line(rows: list[AssessmentRow], summary: FinalSummary | None) -> str:
    """Render the per-agent summary block (counts + interpretation)."""
    red, amber, green = _count_by_rating(rows)
    lines: list[str] = [f"**Summary: {red} Red, {amber} Amber, {green} Green**"]
    if summary is not None:
        if summary.Interpretation:
            lines.append(summary.Interpretation)
        if summary.Overall_Comments:
            lines.append(summary.Overall_Comments)
    return "\n\n".join(lines)


def _render_agent_markdown(agent_type: str, result: AgentResult | None) -> str:
    """Render one agent's section as markdown (heading + table + summary).

    Args:
        agent_type: One of the pipeline's configured agent types.
        result: The agent's validated result, or ``None`` if the agent failed.

    Returns:
        Markdown string including the agent's display heading.  When the
        agent failed a fallback section is returned in place of the table.
    """
    display: str = AGENT_DISPLAY_NAMES[agent_type]
    if result is None:
        return f"## {display}\n\n_Assessment unavailable — agent failed._"

    header: str = "| Question / Query | Rating | Comments | Reference |"
    divider: str = "|------------------|--------|----------|-----------|"
    rows_md: str = "\n".join(_render_row(r) for r in result.assessments)
    summary_md: str = _render_summary_line(result.assessments, result.final_summary)

    parts: list[str] = [f"## {display}", header, divider]
    if rows_md:
        parts.append(rows_md)
    return "\n".join(parts) + f"\n\n{summary_md}"


def _render_scorecard(results: dict[str, AgentResult | None]) -> str:
    """Render the cross-category scorecard table."""
    header: str = "| Category | Red | Amber | Green | Overall |"
    divider: str = "|----------|-----|-------|-------|---------|"
    lines: list[str] = ["### Cross-Category Scorecard", header, divider]

    for agent in _get_pipeline_config().agent_types:
        display: str = AGENT_DISPLAY_NAMES[agent]
        result: AgentResult | None = results.get(agent)
        if result is None:
            lines.append(f"| {display} | — | — | — | Unavailable |")
            continue
        red, amber, green = _count_by_rating(result.assessments)
        overall: str = _overall_label(red, amber, green)
        lines.append(f"| {display} | {red} | {amber} | {green} | {overall} |")

    return "\n".join(lines)


def _overall_label(red: int, amber: int, green: int) -> str:
    """Return a short overall label for a category's RAG counts."""
    if red > 0:
        return "Red" if amber == 0 and green == 0 else "Amber"
    if amber == 0:
        return "Green"
    if green == 0:
        return "Amber"
    return "Amber-Green"


# ---------------------------------------------------------------------------
# Assembly helpers
# ---------------------------------------------------------------------------


def _infer_doc_type(results: dict[str, AgentResult | None]) -> str:
    """Derive the document type label for the compiled report.

    Uses the security agent's interpretation text when available; falls back
    to ``"Security Assessment"`` when the security agent failed or produced
    no summary.

    Args:
        results: Map of ``agent_type`` to ``AgentResult`` (or ``None`` on failure).

    Returns:
        A short document-type label for the front-end response.
    """
    security: AgentResult | None = results.get("security")
    if security is None or security.final_summary is None:
        return _DEFAULT_DOC_TYPE
    interpretation: str = security.final_summary.Interpretation.strip()
    if not interpretation:
        return _DEFAULT_DOC_TYPE
    return interpretation


def _assemble(doc_id: str, results: dict[str, AgentResult | None]) -> CompiledResult:
    """Assemble the compiled report payload from all specialist agent results.

    Args:
        doc_id: The document identifier.
        results: Map of ``agent_type`` to the agent's ``AgentResult`` or ``None``.

    Returns:
        A ``CompiledResult`` whose ``content`` field is a single markdown block
        matching the front-end response contract.
    """
    sections: list[str] = [
        _render_agent_markdown(agent, results.get(agent))
        for agent in _get_pipeline_config().agent_types
    ]
    scorecard: str = _render_scorecard(results)

    body: str = "\n\n".join(
        [
            "# Policy and Design Evaluation",
            *sections,
            "## Final / Full Evaluation Summary",
            scorecard,
        ]
    )

    now: datetime = datetime.now(tz=UTC)
    all_failed: bool = all(result is None for result in results.values())
    status: Literal["completed", "error"] = "error" if all_failed else "completed"

    return CompiledResult(
        docId=doc_id,
        type=_infer_doc_type(results),
        generatedAt=now,
        content=[CompiledContentBlock(type="text", text=body)],
        status=status,
        processedAt=now,
    )


# ---------------------------------------------------------------------------
# Redis fan-in helpers
# ---------------------------------------------------------------------------


async def _store_agent_outcome(
    redis: Any,
    status_msg: AgentStatusMessage,
) -> None:
    """Write an agent's result (or failure marker) into Redis."""
    cache_key: str = key_result(status_msg.docId, status_msg.agentType)
    if status_msg.result is None:
        marker: _AgentFailureMarker = _AgentFailureMarker(
            status="failed",
            error=status_msg.errorMessage or "",
        )
        payload: dict[str, Any] = marker.model_dump()
    else:
        payload = status_msg.result.model_dump()
    await redis_set_json(redis, cache_key, payload, get_cache_config().ttl_result)


async def _read_all_results(
    redis: Any,
    doc_id: str,
) -> dict[str, AgentResult | None]:
    """Read every agent's cached result back from Redis.

    Failure markers ( ``{"status": "failed", ...}`` ) and cache misses are
    both returned as ``None`` so downstream rendering can branch on a single
    condition.
    """
    results: dict[str, AgentResult | None] = {}
    for agent in _get_pipeline_config().agent_types:
        raw: Any = await redis_get_json(redis, key_result(doc_id, agent))
        results[agent] = _coerce_agent_result(raw)
    return results


def _coerce_agent_result(raw: Any) -> AgentResult | None:
    """Map a raw Redis payload to ``AgentResult`` or ``None`` if unusable.

    Returns ``None`` for cache misses, failure markers, or payloads whose
    shape no longer matches ``AgentResult`` (logged for diagnosis).
    """
    if not isinstance(raw, dict):
        return None
    if "assessments" not in raw:
        return None
    try:
        return AgentResult.model_validate(raw)
    except ValidationError as exc:
        logger.error("Cached AgentResult failed validation: raw=%.200s error=%s", raw, exc)
        return None


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point -- delegates to async core."""
    return asyncio.run(_handler(event, context))


async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Async core of the Stage 7 Compile handler.

    Flow:
        1. Validate the SQS envelope and status message body.
        2. Write the agent result (or failure marker) to Redis.
        3. Increment the fan-in counter and, on reaching the total, compile.
        4. When compiling, read all agent results back, assemble the markdown
           report, store the compiled payload, and publish ``DocumentCompiled``.

    Args:
        event: Raw SQS Lambda event dict.
        context: Lambda context object (unused).

    Returns:
        Dict with ``statusCode`` 200 on success.
    """
    sqs_event: _CompileSqsEvent = _CompileSqsEvent.model_validate(event)
    record: _CompileSqsRecord = sqs_event.Records[0]
    status_msg: AgentStatusMessage = AgentStatusMessage.model_validate_json(record.body)

    doc_id: str = status_msg.docId
    agent_type: str = status_msg.agentType

    redis: Any = await _get_redis()

    await _store_agent_outcome(redis, status_msg)

    count: int = await redis_incr(
        redis, key_results_count(doc_id), get_cache_config().ttl_results_count
    )
    total_agents: int = len(_get_pipeline_config().agent_types)
    logger.info(
        "Stage 7 Compile: doc_id=%s agent_type=%s status=%s count=%d/%d",
        doc_id,
        agent_type,
        status_msg.status,
        count,
        total_agents,
    )

    if count >= total_agents:
        await _finalise(redis, doc_id)

    return {"statusCode": 200}


async def _finalise(redis: Any, doc_id: str) -> None:
    """Read all agent results, assemble the report, persist, and publish."""
    logger.info("Stage 7 Compile: fan-in complete, compiling report doc_id=%s", doc_id)
    results: dict[str, AgentResult | None] = await _read_all_results(redis, doc_id)
    compiled: CompiledResult = _assemble(doc_id, results)

    compiled_key: str = key_compiled(doc_id)
    await redis_set_json(
        redis,
        compiled_key,
        compiled.model_dump(mode="json"),
        get_cache_config().ttl_compiled,
    )

    detail: DocumentCompiledDetail = DocumentCompiledDetail(
        docId=doc_id,
        compiledCacheKey=compiled_key,
    )
    publisher: EventBridgePublisher = _get_publisher()
    await publisher.publish("DocumentCompiled", detail.model_dump(by_alias=True))

    logger.info(
        "Stage 7 Compile: published DocumentCompiled doc_id=%s key=%s status=%s",
        doc_id,
        compiled_key,
        compiled.status,
    )
