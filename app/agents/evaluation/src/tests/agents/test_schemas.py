"""Tests for EventBridge detail Pydantic models in src/agents/schemas.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.schemas import (
    AgentCompleteDetail,
    AgentResult,
    AllAgentsCompleteDetail,
    AssessmentRow,
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
