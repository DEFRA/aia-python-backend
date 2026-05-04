"""Tests for the TechnicalAgent.

assess() returns AgentLLMOutput and malformed payloads raise ValueError.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.schemas import AgentLLMOutput, QuestionItem, RawAssessmentRow, Summary
from src.agents.technical_agent import TechnicalAgent
from src.config import TechnicalAgentConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DOCUMENT: str = "Sample policy document text."
_QUESTIONS: list[QuestionItem] = [
    QuestionItem(
        id="aaaaaaaa-0000-0000-0000-000000000001",
        question="Is a ROPA maintained?",
        reference="T1.a",
    ),
    QuestionItem(
        id="aaaaaaaa-0000-0000-0000-000000000002",
        question="Are retention schedules documented?",
        reference="T2.b",
    ),
]


def _make_technical_payload(
    questions: list[QuestionItem],
) -> dict[str, Any]:
    """Build a valid Technical JSON payload using question_ids."""
    rows: list[dict[str, Any]] = []
    for item in questions:
        rows.append(
            {
                "question_id": item.id,
                "Rating": "Green",
                "Comments": "Section 4.2 documents the requirement.",
            }
        )
    return {
        "Technical": {
            "Assessments": rows,
            "Summary": {
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
async def test_technical_agent_assess_returns_agent_llm_output() -> None:
    """assess() should parse the Technical JSON payload into an AgentLLMOutput."""
    payload: dict[str, Any] = _make_technical_payload(_QUESTIONS)
    client: MagicMock = _make_mock_client(json.dumps(payload))
    agent: TechnicalAgent = _make_agent(client)

    result: AgentLLMOutput = await agent.assess(_DOCUMENT, _QUESTIONS)

    assert isinstance(result, AgentLLMOutput)
    assert len(result.rows) == len(_QUESTIONS)
    assert all(isinstance(row, RawAssessmentRow) for row in result.rows)
    assert isinstance(result.summary, Summary)
    assert result.summary.Interpretation == "Strong alignment"


@pytest.mark.asyncio
async def test_technical_agent_rows_have_correct_question_ids() -> None:
    """Rows in the output must carry the question_id values from the input."""
    payload: dict[str, Any] = _make_technical_payload(_QUESTIONS)
    client: MagicMock = _make_mock_client(json.dumps(payload))
    agent: TechnicalAgent = _make_agent(client)

    result: AgentLLMOutput = await agent.assess(_DOCUMENT, _QUESTIONS)

    assert result.rows[0].question_id == "aaaaaaaa-0000-0000-0000-000000000001"
    assert result.rows[1].question_id == "aaaaaaaa-0000-0000-0000-000000000002"


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
        await agent.assess(_DOCUMENT, _QUESTIONS)

    # Case 2: valid JSON but missing top-level ``Technical`` key
    wrong_key_client: MagicMock = _make_mock_client(json.dumps({"Security": {"Assessments": []}}))
    agent = _make_agent(wrong_key_client)
    with pytest.raises(ValueError, match="Could not parse assessment response"):
        await agent.assess(_DOCUMENT, _QUESTIONS)
