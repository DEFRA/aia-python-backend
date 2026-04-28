"""Tests for the local-dev entry point in ``main.py``.

The script orchestrates the same fan-out a Lambda would, but in-process. The
contract under test here is: the runner reads the configured input file,
dispatches one specialist agent per configured ``agent_type`` via
``AGENT_REGISTRY``, and writes the SQS Status output JSON keyed by the
``display_keys`` from ``LocalRunnerConfig``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.schemas import (
    AgentResult,
    AssessmentRow,
    FinalSummary,
    LLMResponseMeta,
    QuestionItem,
    Reference,
    TaggedChunk,
)


def _sample_result(rating: str = "Green") -> AgentResult:
    """Return a minimal valid ``AgentResult`` for mocking."""
    return AgentResult(
        assessments=[
            AssessmentRow(
                Question="Q1",
                Rating=rating,  # type: ignore[arg-type]
                Comments="OK.",
                Reference=Reference(text="G1.a", url="https://example.test/"),
            ),
        ],
        metadata=LLMResponseMeta(
            model="test-model",
            input_tokens=10,
            output_tokens=5,
            stop_reason="end_turn",
        ),
        final_summary=FinalSummary(
            Interpretation="Strong alignment",
            Overall_Comments="OK.",
        ),
    )


@pytest.mark.asyncio
async def test_run_pipeline_dispatches_via_registry_and_writes_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_pipeline`` should dispatch every configured agent_type via the
    registry and write the combined output JSON keyed by ``display_keys``.
    """
    # Source document for the mock SQS body -- contents are irrelevant
    # because ``_parse_bytes`` is patched out.
    doc_path: Path = tmp_path / "doc.pdf"
    doc_path.write_bytes(b"%PDF-1.4 stub")
    output_path: Path = tmp_path / "out.json"

    questions: list[QuestionItem] = [QuestionItem(question="Q1", reference="G1.a")]
    tagged_chunks: list[TaggedChunk] = [
        TaggedChunk(
            chunk_index=0,
            page=1,
            is_heading=False,
            text="body",
            relevant=True,
            tags=["authentication"],
            reason=None,
        )
    ]

    mock_security: MagicMock = MagicMock()
    mock_security.assess = AsyncMock(return_value=_sample_result("Green"))
    mock_security_cls: MagicMock = MagicMock(return_value=mock_security)

    mock_governance: MagicMock = MagicMock()
    mock_governance.assess = AsyncMock(return_value=_sample_result("Amber"))
    mock_governance_cls: MagicMock = MagicMock(return_value=mock_governance)

    with (
        patch("main._parse_bytes", return_value=[{"chunk_index": 0, "text": "x"}]),
        patch("main.TaggingAgent") as tagging_cls,
        patch(
            "main.extract_sections_for_agent",
            return_value=[{"is_heading": False, "text": "body"}],
        ),
        patch(
            "main.load_assessment_from_file",
            return_value=(questions, "https://example.test/"),
        ),
        patch.dict(
            "src.handlers.agent.AGENT_REGISTRY",
            {"security": mock_security_cls, "governance": mock_governance_cls},
        ),
        patch("main.anthropic") as mock_anthropic_mod,
    ):
        tagging_cls.return_value.tag = AsyncMock(return_value=tagged_chunks)
        mock_anthropic_mod.AsyncAnthropicBedrock.return_value = MagicMock()

        from main import run_pipeline

        s3_key: str = str(doc_path.relative_to(doc_path.parent))
        # Resolve the doc against tmp_path by monkey-patching _EVAL_DIR.
        monkeypatch.setattr("main._EVAL_DIR", doc_path.parent)

        result: dict[str, Any] = await run_pipeline(
            s3_key=s3_key,
            doc_id="UUID-test",
            output_path=output_path,
        )

    # Both agents dispatched via the registry
    mock_security_cls.assert_called_once()
    mock_governance_cls.assert_called_once()
    mock_security.assess.assert_awaited_once()
    mock_governance.assess.assert_awaited_once()

    # Output JSON has the SQS Status shape with the configured display keys
    assert output_path.is_file()
    written: dict[str, Any] = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["docId"] == "UUID-test"
    assert "Security" in written
    assert "Governance" in written
    assert written["Security"]["Assessments"][0]["Rating"] == "Green"
    assert written["Governance"]["Assessments"][0]["Rating"] == "Amber"
    assert result == written
