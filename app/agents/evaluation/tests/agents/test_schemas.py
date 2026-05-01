"""Tests for EventBridge detail Pydantic models in src/agents/schemas.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.schemas import (
    AgentCompleteDetail,
    AgentResult,
    AssessmentRow,
    DocumentParsedDetail,
    DocumentTaggedDetail,
    FinalSummary,
    InlinePayload,
    LLMResponseMeta,
    S3KeyPayload,
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
# PayloadEnvelope discriminated union (InlinePayload vs S3KeyPayload)
# ---------------------------------------------------------------------------


def test_inline_payload_validates() -> None:
    """An inline payload exposes its serialised JSON string."""
    p = InlinePayload(inline='{"foo":"bar"}')
    assert p.inline == '{"foo":"bar"}'


def test_s3key_payload_validates() -> None:
    """An s3Key payload exposes the S3 object key."""
    p = S3KeyPayload(s3Key="state/doc-1/chunks.json")
    assert p.s3Key == "state/doc-1/chunks.json"


# ---------------------------------------------------------------------------
# DocumentParsedDetail — payload envelope replaces chunksCacheKey/contentHash
# ---------------------------------------------------------------------------


def test_document_parsed_detail_accepts_inline_payload() -> None:
    """DocumentParsedDetail accepts an inline payload envelope."""
    detail = DocumentParsedDetail(
        document_id="doc-1",
        payload={"inline": '[{"chunk_index": 0}]'},
    )
    assert detail.document_id == "doc-1"
    assert isinstance(detail.payload, InlinePayload)
    assert detail.payload.inline == '[{"chunk_index": 0}]'


def test_document_parsed_detail_accepts_s3_payload() -> None:
    """DocumentParsedDetail accepts an S3 key payload envelope."""
    detail = DocumentParsedDetail(
        document_id="doc-1",
        payload={"s3Key": "state/doc-1/chunks.json"},
    )
    assert isinstance(detail.payload, S3KeyPayload)
    assert detail.payload.s3Key == "state/doc-1/chunks.json"


def test_document_parsed_detail_rejects_envelope_with_neither() -> None:
    """An envelope containing neither inline nor s3Key fails validation."""
    with pytest.raises(ValidationError):
        DocumentParsedDetail(document_id="doc-1", payload={})


def test_document_parsed_detail_no_legacy_fields() -> None:
    """DocumentParsedDetail must not require the removed chunksCacheKey / contentHash."""
    detail = DocumentParsedDetail(document_id="doc-1", payload={"inline": "[]"})
    assert not hasattr(detail, "chunksCacheKey")
    assert not hasattr(detail, "contentHash")


# ---------------------------------------------------------------------------
# DocumentTaggedDetail — payload envelope replaces taggedCacheKey/contentHash
# ---------------------------------------------------------------------------


def test_document_tagged_detail_accepts_inline_payload() -> None:
    """DocumentTaggedDetail accepts an inline payload envelope."""
    detail = DocumentTaggedDetail(
        document_id="doc-1",
        payload={"inline": "[]"},
    )
    assert detail.document_id == "doc-1"
    assert isinstance(detail.payload, InlinePayload)


def test_document_tagged_detail_accepts_s3_payload() -> None:
    """DocumentTaggedDetail accepts an S3 key payload envelope."""
    detail = DocumentTaggedDetail(
        document_id="doc-1",
        payload={"s3Key": "state/doc-1/tagged.json"},
    )
    assert isinstance(detail.payload, S3KeyPayload)


def test_document_tagged_detail_no_legacy_fields() -> None:
    """DocumentTaggedDetail must not require the removed taggedCacheKey / contentHash."""
    detail = DocumentTaggedDetail(document_id="doc-1", payload={"inline": "[]"})
    assert not hasattr(detail, "taggedCacheKey")
    assert not hasattr(detail, "contentHash")


# ---------------------------------------------------------------------------
# SectionsReadyDetail — agent-type literal preserved
# ---------------------------------------------------------------------------


def test_sections_ready_detail_only_accepts_two_agent_types() -> None:
    """SectionsReadyDetail must accept only the two surviving agents."""
    for agent in ("security", "technical"):
        detail = SectionsReadyDetail(document_id="doc-1", agentType=agent)
        assert detail.agentType == agent


def test_sections_ready_detail_rejects_legacy_agent_types() -> None:
    """SectionsReadyDetail must reject the four removed specialist agent types."""
    for legacy in ("data", "risk", "ea", "solution"):
        with pytest.raises(ValidationError):
            SectionsReadyDetail(document_id="doc-1", agentType=legacy)


def test_sections_ready_detail_invalid_agent_type() -> None:
    """SectionsReadyDetail should reject agent types not in the allowed literal."""
    with pytest.raises(ValidationError):
        SectionsReadyDetail(document_id="doc-1", agentType="unknown")


# ---------------------------------------------------------------------------
# AgentCompleteDetail
# ---------------------------------------------------------------------------


def test_agent_complete_detail() -> None:
    """AgentCompleteDetail should accept and expose document_id and agentType."""
    detail = AgentCompleteDetail(document_id="doc-1", agentType="security")
    assert detail.agentType == "security"


# ---------------------------------------------------------------------------
# Removed-model guard — these models were deleted with stages 7-9
# ---------------------------------------------------------------------------


def test_removed_models_are_no_longer_importable() -> None:
    """Stages 7-9 detail models were removed in plan 11."""
    from src.agents import schemas

    for removed in (
        "AllAgentsCompleteDetail",
        "DocumentCompiledDetail",
        "ResultPersistedDetail",
        "DocumentMovedDetail",
        "FinaliseReadyDetail",
        "PipelineCompleteDetail",
        "CompiledContentBlock",
        "CompiledResult",
    ):
        assert not hasattr(schemas, removed), f"{removed} should be removed"
