"""Tests for the Stage 7 Compile Lambda handler.

Covers:
- ``_render_agent_markdown`` output for successful and failed agents
- ``_infer_doc_type`` fallback behaviour
- ``_assemble`` compiled report structure
- ``_handler`` Redis writes, fan-in counter, and EventBridge publish
- ``lambda_handler`` entry point delegating to the async core
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis as fakeredis
import pytest

from src.agents.schemas import (
    AgentResult,
    AgentStatusMessage,
    AssessmentRow,
    CompiledResult,
    FinalSummary,
    LLMResponseMeta,
)
from src.handlers.compile import (
    AGENT_DISPLAY_NAMES,
    AGENT_TYPES,
    _assemble,
    _handler,
    _infer_doc_type,
    _render_agent_markdown,
    lambda_handler,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_agent_result(doc_type: str | None = None) -> AgentResult:
    """Return a sample AgentResult with a couple of rows and a summary."""
    summary_text: str = "Solid overall coverage."
    if doc_type is not None:
        summary_text = f"{doc_type} — {summary_text}"
    return AgentResult(
        assessments=[
            AssessmentRow(
                Question="Are auth controls defined?",
                Coverage="Green",
                Evidence="RBAC and MFA documented.",
            ),
            AssessmentRow(
                Question="Is IR end-to-end?",
                Coverage="Amber",
                Evidence="SLA mapping weak.",
            ),
        ],
        metadata=LLMResponseMeta(
            model="claude-sonnet-4-6",
            input_tokens=10,
            output_tokens=5,
            stop_reason="end_turn",
        ),
        final_summary=FinalSummary(
            Interpretation=summary_text,
            Overall_Comments="Close IR SLA gap.",
        ),
    )


# ---------------------------------------------------------------------------
# _render_agent_markdown
# ---------------------------------------------------------------------------


def test_render_agent_markdown_valid_result_contains_heading_and_rows() -> None:
    """Rendered markdown should include the display heading, table, and summary."""
    result: AgentResult = _sample_agent_result()
    markdown: str = _render_agent_markdown("security", result)

    assert f"## {AGENT_DISPLAY_NAMES['security']}" in markdown
    assert "| Question / Query | Rating | Comments | Reference |" in markdown
    assert "Are auth controls defined?" in markdown
    assert "RBAC and MFA documented." in markdown
    assert "Solid overall coverage." in markdown


def test_render_agent_markdown_none_result_returns_failure_section() -> None:
    """A None result should render a clearly-marked failure section."""
    markdown: str = _render_agent_markdown("data", None)

    assert f"## {AGENT_DISPLAY_NAMES['data']}" in markdown
    # Fallback should not pretend to have assessment rows
    assert "| Question / Query | Rating | Comments | Reference |" not in markdown
    assert "unavailable" in markdown.lower() or "failed" in markdown.lower()


def test_render_agent_markdown_escapes_pipes_in_evidence() -> None:
    """Pipe characters in evidence must not break the markdown table."""
    result: AgentResult = AgentResult(
        assessments=[
            AssessmentRow(
                Question="Q?",
                Coverage="Green",
                Evidence="Evidence with | pipe and\nnewline",
            ),
        ],
        metadata=LLMResponseMeta(model="m", input_tokens=1, output_tokens=1, stop_reason=None),
        final_summary=None,
    )
    markdown: str = _render_agent_markdown("risk", result)
    # Expect the row to exist on a single line with no raw pipe/newline leaking in
    row_lines: list[str] = [line for line in markdown.splitlines() if line.startswith("| Q? ")]
    assert len(row_lines) == 1
    assert "\n" not in row_lines[0]


# ---------------------------------------------------------------------------
# _infer_doc_type
# ---------------------------------------------------------------------------


def test_infer_doc_type_uses_solution_agent_interpretation() -> None:
    """_infer_doc_type should pick doc_type text up from the solution agent summary."""
    solution_result: AgentResult = _sample_agent_result(doc_type="Solution Design")
    results: dict[str, AgentResult | None] = {
        "security": None,
        "data": None,
        "risk": None,
        "ea": None,
        "solution": solution_result,
    }
    doc_type: str = _infer_doc_type(results)
    assert solution_result.final_summary is not None
    assert doc_type == solution_result.final_summary.Interpretation
    assert "Solution Design" in doc_type


def test_infer_doc_type_defaults_when_solution_missing() -> None:
    """_infer_doc_type should fall back to a default when no solution agent result exists."""
    results: dict[str, AgentResult | None] = {a: None for a in AGENT_TYPES}
    assert _infer_doc_type(results) == "Security Assessment"


def test_infer_doc_type_defaults_when_solution_has_no_summary() -> None:
    """_infer_doc_type should fall back to default when solution has no final_summary."""
    no_summary: AgentResult = AgentResult(
        assessments=[],
        metadata=LLMResponseMeta(model="m", input_tokens=0, output_tokens=0, stop_reason=None),
        final_summary=None,
    )
    results: dict[str, AgentResult | None] = {a: None for a in AGENT_TYPES}
    results["solution"] = no_summary
    assert _infer_doc_type(results) == "Security Assessment"


# ---------------------------------------------------------------------------
# _assemble
# ---------------------------------------------------------------------------


def test_assemble_all_agents_successful_produces_single_text_block() -> None:
    """_assemble should produce a CompiledResult with one text content block."""
    results: dict[str, AgentResult | None] = {
        agent: _sample_agent_result() for agent in AGENT_TYPES
    }
    compiled: CompiledResult = _assemble("doc-1", results)

    assert compiled.docId == "doc-1"
    assert compiled.status == "completed"
    assert len(compiled.content) == 1
    assert compiled.content[0].type == "text"
    body: str = compiled.content[0].text
    for agent in AGENT_TYPES:
        assert AGENT_DISPLAY_NAMES[agent] in body
    assert "Cross-Category Scorecard" in body


def test_assemble_handles_one_failed_agent() -> None:
    """_assemble should degrade gracefully with a failed agent in the mix."""
    results: dict[str, AgentResult | None] = {
        agent: _sample_agent_result() for agent in AGENT_TYPES
    }
    results["risk"] = None
    compiled: CompiledResult = _assemble("doc-2", results)

    assert compiled.status == "completed"
    body: str = compiled.content[0].text
    assert AGENT_DISPLAY_NAMES["risk"] in body


def test_assemble_marks_error_when_all_agents_failed() -> None:
    """_assemble should mark status='error' when every agent failed."""
    results: dict[str, AgentResult | None] = {agent: None for agent in AGENT_TYPES}
    compiled: CompiledResult = _assemble("doc-3", results)
    assert compiled.status == "error"


# ---------------------------------------------------------------------------
# _handler
# ---------------------------------------------------------------------------


def _build_sqs_event(msg: AgentStatusMessage) -> dict[str, Any]:
    """Wrap an AgentStatusMessage in an SQS Lambda event envelope."""
    return {"Records": [{"body": msg.model_dump_json()}]}


@pytest.mark.asyncio
async def test_handler_writes_result_and_does_not_compile_when_counter_below_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under-threshold counter should skip compile and not publish DocumentCompiled."""
    monkeypatch.setenv("REDIS_HOST", "localhost")

    redis: fakeredis.FakeRedis = fakeredis.FakeRedis(decode_responses=True)
    publisher: MagicMock = MagicMock()
    publisher.publish = AsyncMock()

    msg: AgentStatusMessage = AgentStatusMessage(
        docId="doc-x",
        agentType="security",
        status="completed",
        result=_sample_agent_result(),
        durationMs=100.0,
        completedAt="2026-04-15T12:00:00Z",
    )

    with (
        patch("src.handlers.compile._get_redis", AsyncMock(return_value=redis)),
        patch("src.handlers.compile._get_publisher", return_value=publisher),
    ):
        result: dict[str, Any] = await _handler(_build_sqs_event(msg), {})

    assert result == {"statusCode": 200}
    stored: str | None = await redis.get("result:doc-x:security")
    assert stored is not None
    publisher.publish.assert_not_called()


@pytest.mark.asyncio
async def test_handler_triggers_compile_when_counter_hits_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When all 5 agent statuses arrive, compile and publish DocumentCompiled."""
    monkeypatch.setenv("REDIS_HOST", "localhost")

    redis: fakeredis.FakeRedis = fakeredis.FakeRedis(decode_responses=True)
    publisher: MagicMock = MagicMock()
    publisher.publish = AsyncMock()

    with (
        patch("src.handlers.compile._get_redis", AsyncMock(return_value=redis)),
        patch("src.handlers.compile._get_publisher", return_value=publisher),
    ):
        for agent in AGENT_TYPES:
            msg: AgentStatusMessage = AgentStatusMessage(
                docId="doc-y",
                agentType=agent,
                status="completed",
                result=_sample_agent_result(),
                durationMs=100.0,
                completedAt="2026-04-15T12:00:00Z",
            )
            await _handler(_build_sqs_event(msg), {})

    compiled_raw: str | None = await redis.get("compiled:doc-y")
    assert compiled_raw is not None
    payload: dict[str, Any] = json.loads(compiled_raw)
    assert payload["docId"] == "doc-y"
    assert payload["status"] == "completed"

    publisher.publish.assert_called_once()
    detail_type, detail = publisher.publish.call_args.args
    assert detail_type == "DocumentCompiled"
    assert detail["docId"] == "doc-y"
    assert detail["compiledCacheKey"] == "compiled:doc-y"


@pytest.mark.asyncio
async def test_handler_stores_failed_agent_with_error_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed status messages should store a failure marker rather than a result."""
    monkeypatch.setenv("REDIS_HOST", "localhost")

    redis: fakeredis.FakeRedis = fakeredis.FakeRedis(decode_responses=True)
    publisher: MagicMock = MagicMock()
    publisher.publish = AsyncMock()

    msg: AgentStatusMessage = AgentStatusMessage(
        docId="doc-z",
        agentType="data",
        status="failed",
        result=None,
        durationMs=50.0,
        completedAt="2026-04-15T12:00:00Z",
        errorMessage="boom",
    )

    with (
        patch("src.handlers.compile._get_redis", AsyncMock(return_value=redis)),
        patch("src.handlers.compile._get_publisher", return_value=publisher),
    ):
        await _handler(_build_sqs_event(msg), {})

    raw: str | None = await redis.get("result:doc-z:data")
    assert raw is not None
    stored: dict[str, Any] = json.loads(raw)
    assert stored["status"] == "failed"
    assert stored["error"] == "boom"


# ---------------------------------------------------------------------------
# lambda_handler
# ---------------------------------------------------------------------------


def test_lambda_handler_delegates_via_asyncio_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """lambda_handler should delegate to _handler through asyncio.run."""
    captured: dict[str, Any] = {}

    async def fake_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
        captured["event"] = event
        captured["context"] = context
        return {"statusCode": 200}

    with patch("src.handlers.compile._handler", side_effect=fake_handler):
        result: dict[str, Any] = lambda_handler({"Records": []}, {"ctx": 1})

    assert result == {"statusCode": 200}
    assert captured["event"] == {"Records": []}
