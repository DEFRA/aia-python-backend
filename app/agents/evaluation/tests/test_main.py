"""Tests for the local-dev entry point in ``main.py``.

The script orchestrates the same fan-out a Lambda would, but in-process. The
contract under test here is: the script must dispatch via the agent registry,
so calling it with category ``"Governance"`` exercises ``GovernanceAgent`` and
not a hardcoded ``SecurityAgent``.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
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
)


@pytest.fixture(autouse=True)
def _stub_pdf_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the optional ``reportlab``-backed PDF builder so the test runs
    without the third-party dependency installed.

    ``main.py`` only invokes ``build_security_report`` after the agent has
    returned, and the test patches that call out anyway; pre-importing a
    no-op module satisfies the top-level import in ``main.py``.
    """
    if "src.utils.pdf_creator_multipage" not in sys.modules:
        stub: ModuleType = ModuleType("src.utils.pdf_creator_multipage")
        stub.build_security_report = lambda **kwargs: None  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "src.utils.pdf_creator_multipage", stub)
        # ``reportlab`` itself is not imported anywhere else under test, but
        # provide a placeholder so any incidental import resolves cleanly.
        monkeypatch.setitem(sys.modules, "reportlab", SimpleNamespace())


def _sample_result() -> AgentResult:
    """Return a minimal valid ``AgentResult`` for mocking."""
    return AgentResult(
        assessments=[
            AssessmentRow(
                Question="Q1",
                Rating="Green",
                Comments="OK.",
                Reference=Reference(text="G1.a", url="https://ico.org.uk/"),
            ),
        ],
        metadata=LLMResponseMeta(
            model="claude-opus-4-6",
            input_tokens=10,
            output_tokens=5,
            stop_reason="end_turn",
        ),
        final_summary=FinalSummary(Interpretation="Strong alignment", Overall_Comments="OK."),
    )


@pytest.mark.asyncio
async def test_main_accepts_governance_category(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main.run`` should dispatch to the ``GovernanceAgent`` when the supplied
    category is ``"Governance"`` rather than always falling back to ``SecurityAgent``.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    document_path: Any = tmp_path / "doc.md"
    document_path.write_text("Sample policy document.", encoding="utf-8")
    output_pdf: Any = tmp_path / "report.pdf"

    questions: list[QuestionItem] = [QuestionItem(question="Q1", reference="G1.a")]
    category_url: str = "https://ico.org.uk/"

    mock_agent_instance: MagicMock = MagicMock()
    mock_agent_instance.assess = AsyncMock(return_value=_sample_result())
    mock_agent_cls: MagicMock = MagicMock(return_value=mock_agent_instance)

    with (
        patch(
            "main.load_assessment_from_file",
            return_value=(questions, category_url),
        ),
        patch.dict("src.handlers.agent.AGENT_REGISTRY", {"governance": mock_agent_cls}),
        patch("main.build_security_report"),
        patch("main.anthropic") as mock_anthropic_mod,
    ):
        mock_anthropic_mod.AsyncAnthropic.return_value = MagicMock()
        from main import run

        await run(str(document_path), str(output_pdf), "Governance")

    mock_agent_cls.assert_called_once()
    mock_agent_instance.assess.assert_awaited_once()
