"""Tests for EventBridge detail Pydantic models in src/agents/schemas.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.schemas import (
    AgentCompleteDetail,
    AgentLLMOutput,
    AgentResult,
    AssessmentRow,
    DocumentParsedDetail,
    DocumentTaggedDetail,
    InlinePayload,
    LLMResponseMeta,
    QuestionItem,
    RawAssessmentRow,
    S3KeyPayload,
    SectionsReadyDetail,
    Summary,
)

# ---------------------------------------------------------------------------
# Ensure existing models are untouched
# ---------------------------------------------------------------------------


def test_existing_models_still_importable() -> None:
    """Existing models must not be broken by adding new ones."""
    assert AssessmentRow is not None
    assert Summary is not None
    assert LLMResponseMeta is not None
    assert AgentResult is not None


# ---------------------------------------------------------------------------
# QuestionItem gains id field
# ---------------------------------------------------------------------------


def test_question_item_requires_id() -> None:
    """QuestionItem must require an id field."""
    q = QuestionItem(
        id="aaaaaaaa-0000-0000-0000-000000000001",
        question="Is MFA enabled?",
        reference="C1.a",
    )
    assert q.id == "aaaaaaaa-0000-0000-0000-000000000001"
    assert q.question == "Is MFA enabled?"
    assert q.reference == "C1.a"


# ---------------------------------------------------------------------------
# AssessmentRow.Reference is now a plain string
# ---------------------------------------------------------------------------


def test_assessment_row_reference_is_string() -> None:
    """AssessmentRow.Reference must be a plain string, not a nested object."""
    row = AssessmentRow(
        Question="Is MFA enabled?",
        Rating="Green",
        Comments="MFA is enforced.",
        Reference="C1.a",
    )
    assert isinstance(row.Reference, str)
    assert row.Reference == "C1.a"


# ---------------------------------------------------------------------------
# Summary (renamed from FinalSummary)
# ---------------------------------------------------------------------------


def test_summary_validates() -> None:
    """Summary must expose Interpretation and Overall_Comments."""
    s = Summary(
        Interpretation="Strong alignment",
        Overall_Comments="All requirements addressed.",
    )
    assert s.Interpretation == "Strong alignment"
    assert s.Overall_Comments == "All requirements addressed."


# ---------------------------------------------------------------------------
# RawAssessmentRow
# ---------------------------------------------------------------------------


def test_raw_assessment_row_validates() -> None:
    """RawAssessmentRow must expose question_id, Rating, and Comments."""
    row = RawAssessmentRow(
        question_id="aaaaaaaa-0000-0000-0000-000000000001",
        Rating="Amber",
        Comments="Partial coverage.",
    )
    assert row.question_id == "aaaaaaaa-0000-0000-0000-000000000001"
    assert row.Rating == "Amber"


def test_raw_assessment_row_rejects_invalid_rating() -> None:
    """RawAssessmentRow must reject ratings outside Green/Amber/Red."""
    with pytest.raises(ValidationError):
        RawAssessmentRow(
            question_id="some-id",
            Rating="Yellow",  # type: ignore[arg-type]
            Comments="Invalid.",
        )


# ---------------------------------------------------------------------------
# AgentLLMOutput
# ---------------------------------------------------------------------------


def test_agent_llm_output_validates() -> None:
    """AgentLLMOutput must expose rows and summary."""
    output = AgentLLMOutput(
        rows=[
            RawAssessmentRow(
                question_id="aaaaaaaa-0000-0000-0000-000000000001",
                Rating="Green",
                Comments="Fully addressed.",
            )
        ],
        summary=Summary(
            Interpretation="Strong alignment",
            Overall_Comments="No gaps found.",
        ),
    )
    assert len(output.rows) == 1
    assert output.summary.Interpretation == "Strong alignment"


# ---------------------------------------------------------------------------
# AgentResult new fields
# ---------------------------------------------------------------------------


def test_agent_result_new_fields() -> None:
    """AgentResult must expose policy_doc_filename, policy_doc_url, assessments, summary."""
    result = AgentResult(
        policy_doc_filename="security_policy.pdf",
        policy_doc_url="https://example.com/security_policy.pdf",
        assessments=[
            AssessmentRow(
                Question="Is MFA enabled?",
                Rating="Green",
                Comments="MFA is enforced.",
                Reference="C1.a",
            )
        ],
        summary=Summary(
            Interpretation="Strong alignment",
            Overall_Comments="All requirements addressed.",
        ),
    )
    assert result.policy_doc_filename == "security_policy.pdf"
    assert result.policy_doc_url == "https://example.com/security_policy.pdf"
    assert len(result.assessments) == 1
    assert result.summary.Interpretation == "Strong alignment"


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
        "FinalSummary",
        "Reference",
    ):
        assert not hasattr(schemas, removed), f"{removed} should be removed"
