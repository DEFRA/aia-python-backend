"""Tests for the TechnicalAgent.

Mirrors the SecurityAgent test contract: assess() returns AgentResult, the
authoritative reference is echoed verbatim, and malformed payloads raise
ValueError.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.schemas import AgentResult, AssessmentRow, FinalSummary, QuestionItem
from src.agents.technical_agent import TechnicalAgent
from src.config import TechnicalAgentConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DOCUMENT: str = "Sample policy document text."
_CATEGORY_URL: str = "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/"
_QUESTIONS: list[QuestionItem] = [
    QuestionItem(question="Is a ROPA maintained?", reference="T1.a"),
    QuestionItem(question="Are retention schedules documented?", reference="T2.b"),
]


def _make_technical_payload(
    questions: list[QuestionItem],
    *,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a valid Technical JSON payload echoing the supplied references."""
    overrides = overrides or {}
    rows: list[dict[str, Any]] = []
    for item in questions:
        rows.append(
            {
                "Question": item.question,
                "Rating": "Green",
                "Comments": "Section 4.2 documents the requirement.",
                "Reference": {
                    "text": overrides.get(item.reference, item.reference),
                    "url": _CATEGORY_URL,
                },
            }
        )
    return {
        "Technical": {
            "Assessments": rows,
            "Final_Summary": {
                "Interpretation": "Strong alignment",
                "Overall_Comments": "All requirements addressed.",
            },
        }
    }


def _make_mock_response(text: str) -> MagicMock:
    """Build a mock Anthropic Message with the supplied text body."""
    mock_response: MagicMock = MagicMock()
    mock_response.content = [MagicMock(text=text)]
    mock_response.model = "test-model"
    mock_response.usage = MagicMock(input_tokens=120, output_tokens=60)
    mock_response.stop_reason = "end_turn"
    return mock_response


def _make_mock_client(text: str) -> MagicMock:
    """Build a mock AsyncAnthropic client returning a single canned response."""
    mock_client: MagicMock = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_mock_response(text))
    return mock_client


def _make_agent(client: MagicMock) -> TechnicalAgent:
    config: TechnicalAgentConfig = TechnicalAgentConfig(ANTHROPIC_API_KEY="test-key")
    return TechnicalAgent(client=client, agent_config=config)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_technical_agent_assess_returns_agent_result() -> None:
    """assess() should parse the Technical JSON payload into an AgentResult."""
    payload: dict[str, Any] = _make_technical_payload(_QUESTIONS)
    client: MagicMock = _make_mock_client(json.dumps(payload))
    agent: TechnicalAgent = _make_agent(client)

    result: AgentResult = await agent.assess(_DOCUMENT, _QUESTIONS, _CATEGORY_URL)

    assert isinstance(result, AgentResult)
    assert len(result.assessments) == len(_QUESTIONS)
    assert all(isinstance(row, AssessmentRow) for row in result.assessments)
    assert isinstance(result.final_summary, FinalSummary)
    assert result.metadata.input_tokens == 120
    assert result.metadata.output_tokens == 60


@pytest.mark.asyncio
async def test_technical_agent_validates_reference_echo() -> None:
    """If the LLM echoes the wrong reference, the value flows through verbatim.

    The agent does not silently rewrite the reference; downstream consumers
    rely on this behaviour to spot drift between the prompt contract and the
    actual model output.
    """
    payload: dict[str, Any] = _make_technical_payload(
        _QUESTIONS,
        overrides={"T1.a": "WRONG-REF"},
    )
    client: MagicMock = _make_mock_client(json.dumps(payload))
    agent: TechnicalAgent = _make_agent(client)

    result: AgentResult = await agent.assess(_DOCUMENT, _QUESTIONS, _CATEGORY_URL)

    assert result.assessments[0].Reference.text == "WRONG-REF"
    assert result.assessments[1].Reference.text == "T2.b"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_technical_agent_raises_on_invalid_payload() -> None:
    """Unparseable JSON or a missing ``Technical`` top-level key must raise ValueError."""
    # Case 1: malformed JSON
    bad_client: MagicMock = _make_mock_client("Not JSON at all")
    agent: TechnicalAgent = _make_agent(bad_client)
    with pytest.raises(ValueError, match="Could not parse assessment response"):
        await agent.assess(_DOCUMENT, _QUESTIONS, _CATEGORY_URL)

    # Case 2: valid JSON but missing top-level ``Technical`` key
    wrong_key_client: MagicMock = _make_mock_client(json.dumps({"Security": {"Assessments": []}}))
    agent = _make_agent(wrong_key_client)
    with pytest.raises(ValueError, match="Could not parse assessment response"):
        await agent.assess(_DOCUMENT, _QUESTIONS, _CATEGORY_URL)
