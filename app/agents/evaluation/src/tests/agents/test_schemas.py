"""Tests for EventBridge detail Pydantic models in src/agents/schemas.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.agents.schemas import (
    AgentCompleteDetail,
    AgentResult,
    AgentStatusMessage,
    AllAgentsCompleteDetail,
    AssessmentRow,
    CompiledContentBlock,
    CompiledResult,
    DocumentCompiledDetail,
    DocumentMovedDetail,
    DocumentParsedDetail,
    DocumentTaggedDetail,
    FinaliseReadyDetail,
    FinalSummary,
    LLMResponseMeta,
    PipelineCompleteDetail,
    ResultPersistedDetail,
    SectionsReadyDetail,
)


def _sample_agent_result() -> AgentResult:
    """Return a minimal valid AgentResult for schema fixtures."""
    return AgentResult(
        assessments=[],
        metadata=LLMResponseMeta(model="m", input_tokens=0, output_tokens=0, stop_reason=None),
        final_summary=None,
    )


# ---------------------------------------------------------------------------
# Ensure existing models are untouched
# ---------------------------------------------------------------------------


def test_existing_models_still_importable() -> None:
    """Existing models must not be broken by adding new ones."""
    assert AssessmentRow is not None
    assert FinalSummary is not None
    assert LLMResponseMeta is not None
    assert AgentResult is not None


# ---------------------------------------------------------------------------
# DocumentParsedDetail
# ---------------------------------------------------------------------------


def test_document_parsed_detail() -> None:
    """DocumentParsedDetail should accept and expose docId, chunksCacheKey, and contentHash."""
    detail = DocumentParsedDetail(docId="doc-1", chunksCacheKey="chunks:abc", contentHash="abc")
    assert detail.docId == "doc-1"
    assert detail.chunksCacheKey == "chunks:abc"
    assert detail.contentHash == "abc"


# ---------------------------------------------------------------------------
# DocumentTaggedDetail
# ---------------------------------------------------------------------------


def test_document_tagged_detail() -> None:
    """DocumentTaggedDetail should accept and expose docId, taggedCacheKey, and contentHash."""
    detail = DocumentTaggedDetail(docId="doc-1", taggedCacheKey="tagged:abc", contentHash="abc")
    assert detail.docId == "doc-1"
    assert detail.taggedCacheKey == "tagged:abc"


# ---------------------------------------------------------------------------
# SectionsReadyDetail
# ---------------------------------------------------------------------------


def test_sections_ready_detail_valid_agent_type() -> None:
    """SectionsReadyDetail should accept all five valid agent types."""
    for agent in ("security", "data", "risk", "ea", "solution"):
        detail = SectionsReadyDetail(docId="doc-1", agentType=agent)
        assert detail.agentType == agent


def test_sections_ready_detail_invalid_agent_type() -> None:
    """SectionsReadyDetail should reject agent types not in the allowed literal."""
    with pytest.raises(ValidationError):
        SectionsReadyDetail(docId="doc-1", agentType="unknown")


# ---------------------------------------------------------------------------
# AgentCompleteDetail
# ---------------------------------------------------------------------------


def test_agent_complete_detail() -> None:
    """AgentCompleteDetail should accept and expose docId and agentType."""
    detail = AgentCompleteDetail(docId="doc-1", agentType="security")
    assert detail.agentType == "security"


# ---------------------------------------------------------------------------
# AllAgentsCompleteDetail
# ---------------------------------------------------------------------------


def test_all_agents_complete_detail() -> None:
    """AllAgentsCompleteDetail should accept and expose docId."""
    detail = AllAgentsCompleteDetail(docId="doc-1")
    assert detail.docId == "doc-1"


# ---------------------------------------------------------------------------
# DocumentCompiledDetail
# ---------------------------------------------------------------------------


def test_document_compiled_detail() -> None:
    """DocumentCompiledDetail should accept and expose docId and compiledCacheKey."""
    detail = DocumentCompiledDetail(docId="doc-1", compiledCacheKey="compiled:doc-1")
    assert detail.compiledCacheKey == "compiled:doc-1"


# ---------------------------------------------------------------------------
# ResultPersistedDetail
# ---------------------------------------------------------------------------


def test_result_persisted_detail() -> None:
    """ResultPersistedDetail should accept and expose docId."""
    detail = ResultPersistedDetail(docId="doc-1")
    assert detail.docId == "doc-1"


# ---------------------------------------------------------------------------
# DocumentMovedDetail
# ---------------------------------------------------------------------------


def test_document_moved_detail_valid_destinations() -> None:
    """DocumentMovedDetail should accept 'completed' and 'error' destinations."""
    for dest in ("completed", "error"):
        detail = DocumentMovedDetail(docId="doc-1", destination=dest)
        assert detail.destination == dest


def test_document_moved_detail_invalid_destination() -> None:
    """DocumentMovedDetail should reject destinations not in the allowed literal."""
    with pytest.raises(ValidationError):
        DocumentMovedDetail(docId="doc-1", destination="archive")


# ---------------------------------------------------------------------------
# FinaliseReadyDetail
# ---------------------------------------------------------------------------


def test_finalise_ready_detail() -> None:
    """FinaliseReadyDetail should accept and expose docId."""
    detail = FinaliseReadyDetail(docId="doc-1")
    assert detail.docId == "doc-1"


# ---------------------------------------------------------------------------
# PipelineCompleteDetail
# ---------------------------------------------------------------------------


def test_pipeline_complete_detail_valid_statuses() -> None:
    """PipelineCompleteDetail should accept 'completed' and 'error' statuses."""
    for status in ("completed", "error"):
        detail = PipelineCompleteDetail(docId="doc-1", status=status)
        assert detail.status == status


def test_pipeline_complete_detail_invalid_status() -> None:
    """PipelineCompleteDetail should reject statuses not in the allowed literal."""
    with pytest.raises(ValidationError):
        PipelineCompleteDetail(docId="doc-1", status="unknown")


# ---------------------------------------------------------------------------
# AgentStatusMessage (Stage 6 -> Stage 7 via SQS Status queue)
# ---------------------------------------------------------------------------


def test_agent_status_message_completed_round_trip() -> None:
    """AgentStatusMessage with status='completed' should round-trip via JSON."""
    duration: float = 1234.5
    msg: AgentStatusMessage = AgentStatusMessage(
        docId="doc-1",
        agentType="security",
        status="completed",
        result=_sample_agent_result(),
        durationMs=duration,
        completedAt="2026-04-15T12:00:00Z",
    )
    encoded: str = msg.model_dump_json()
    decoded: AgentStatusMessage = AgentStatusMessage.model_validate_json(encoded)
    assert decoded.docId == "doc-1"
    assert decoded.agentType == "security"
    assert decoded.status == "completed"
    assert decoded.durationMs == duration
    assert decoded.errorMessage is None
    assert isinstance(decoded.result, AgentResult)


def test_agent_status_message_failed_allows_null_result() -> None:
    """AgentStatusMessage should allow result=None when status='failed'."""
    msg: AgentStatusMessage = AgentStatusMessage(
        docId="doc-1",
        agentType="data",
        status="failed",
        result=None,
        durationMs=800.0,
        completedAt="2026-04-15T12:00:00Z",
        errorMessage="Claude returned garbage",
    )
    assert msg.status == "failed"
    assert msg.result is None
    assert msg.errorMessage == "Claude returned garbage"


def test_agent_status_message_rejects_invalid_status() -> None:
    """AgentStatusMessage should reject statuses not in the allowed literal."""
    with pytest.raises(ValidationError):
        AgentStatusMessage(
            docId="doc-1",
            agentType="risk",
            status="pending",
            result=None,
            durationMs=0.0,
            completedAt="2026-04-15T12:00:00Z",
        )


def test_agent_status_message_requires_core_fields() -> None:
    """AgentStatusMessage must reject messages missing required fields."""
    with pytest.raises(ValidationError):
        AgentStatusMessage.model_validate({"docId": "doc-1"})


# ---------------------------------------------------------------------------
# CompiledResult (Stage 7 assembled output)
# ---------------------------------------------------------------------------


def test_compiled_result_round_trip() -> None:
    """CompiledResult should round-trip through JSON without losing fields."""
    generated_at: datetime = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    result: CompiledResult = CompiledResult(
        docId="doc-1",
        type="Security Assessment",
        generatedAt=generated_at,
        content=[CompiledContentBlock(type="text", text="# Report")],
        status="completed",
        processedAt=generated_at,
    )
    encoded: str = result.model_dump_json()
    decoded: CompiledResult = CompiledResult.model_validate_json(encoded)
    assert decoded.docId == "doc-1"
    assert decoded.type == "Security Assessment"
    assert decoded.status == "completed"
    assert decoded.content[0].type == "text"
    assert decoded.content[0].text == "# Report"


def test_compiled_result_rejects_invalid_status() -> None:
    """CompiledResult should reject statuses not in the allowed literal."""
    now: datetime = datetime.now(tz=UTC)
    with pytest.raises(ValidationError):
        CompiledResult(
            docId="doc-1",
            type="Security Assessment",
            generatedAt=now,
            content=[],
            status="pending",
            processedAt=now,
        )


def test_compiled_result_requires_all_core_fields() -> None:
    """CompiledResult must reject payloads missing required fields."""
    with pytest.raises(ValidationError):
        CompiledResult.model_validate({"docId": "doc-1"})
