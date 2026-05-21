"""Retry behaviour tests for ``SecurityAgent.assess``.

Verifies that the ``@agent_retry()`` decorator applied at import time wraps
``assess`` in tenacity's retry loop with the predicate from
``src.utils.retry._is_transient``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from anthropic import APIConnectionError, APIStatusError, RateLimitError
from pydantic import ValidationError

from app.agents.evaluation.src.agents.schemas import AgentLLMOutput, QuestionItem
from app.agents.evaluation.src.agents.security_agent import SecurityAgent
from app.agents.evaluation.src.config import SecurityAgentConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_DOCUMENT: str = "Sample policy document text."
_QUESTIONS: list[QuestionItem] = [
    QuestionItem(
        id="bbbbbbbb-0000-0000-0000-000000000001",
        question="Is encryption at rest enabled?",
        reference="S1.a",
    ),
    QuestionItem(
        id="bbbbbbbb-0000-0000-0000-000000000002",
        question="Are secrets rotated?",
        reference="S1.b",
    ),
]


def _make_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _make_status_error(status_code: int) -> APIStatusError:
    response: httpx.Response = httpx.Response(
        status_code=status_code, request=_make_request()
    )
    return APIStatusError(message=f"status {status_code}", response=response, body=None)


def _make_rate_limit_error() -> RateLimitError:
    response: httpx.Response = httpx.Response(status_code=429, request=_make_request())
    return RateLimitError(message="rate limited", response=response, body=None)


def _security_payload(questions: list[QuestionItem]) -> dict[str, Any]:
    return {
        "Security": {
            "Assessments": [
                {
                    "question_id": q.id,
                    "Rating": "Green",
                    "Comments": "Documented in section 3.",
                }
                for q in questions
            ],
            "Summary": {
                "Interpretation": "Strong alignment",
                "Overall_Comments": "All checks satisfied.",
            },
        }
    }


def _make_response(text: str) -> MagicMock:
    response: MagicMock = MagicMock()
    response.content = [MagicMock(text=text)]
    response.model = "test-model"
    response.usage = MagicMock(input_tokens=10, output_tokens=10)
    response.stop_reason = "end_turn"
    return response


def _make_agent(client: MagicMock) -> SecurityAgent:
    config: SecurityAgentConfig = SecurityAgentConfig(ANTHROPIC_API_KEY="test-key")
    return SecurityAgent(client=client, agent_config=config)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the underlying asyncio.sleep so tenacity backoffs run instantly."""
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_agent_retries_on_transient_api_error() -> None:
    """Connection / rate-limit errors should retry and eventually succeed."""
    success_text: str = json.dumps(_security_payload(_QUESTIONS))
    client: MagicMock = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=[
            APIConnectionError(request=_make_request()),
            _make_rate_limit_error(),
            _make_response(success_text),
        ],
    )
    agent: SecurityAgent = _make_agent(client)

    result: AgentLLMOutput = await agent.assess(_DOCUMENT, _QUESTIONS)

    assert isinstance(result, AgentLLMOutput)
    assert client.messages.create.await_count == 3


@pytest.mark.asyncio
async def test_security_agent_retries_on_malformed_json_then_succeeds() -> None:
    """Malformed JSON is transient — agent should retry and succeed."""
    success_text: str = json.dumps(_security_payload(_QUESTIONS))
    client: MagicMock = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=[
            _make_response("not json"),
            _make_response(success_text),
        ],
    )
    agent: SecurityAgent = _make_agent(client)

    result: AgentLLMOutput = await agent.assess(_DOCUMENT, _QUESTIONS)

    assert isinstance(result, AgentLLMOutput)
    assert client.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_security_agent_does_not_retry_on_4xx() -> None:
    """A 4xx APIStatusError must propagate immediately without retrying."""
    client: MagicMock = MagicMock()
    client.messages.create = AsyncMock(side_effect=_make_status_error(400))
    agent: SecurityAgent = _make_agent(client)

    with pytest.raises(APIStatusError):
        await agent.assess(_DOCUMENT, _QUESTIONS)

    assert client.messages.create.await_count == 1


@pytest.mark.asyncio
async def test_security_agent_does_not_retry_on_validation_error() -> None:
    """A ValidationError from a malformed payload row is terminal."""
    bad_payload: dict[str, Any] = {
        "Security": {
            "Assessments": [
                {
                    # missing "question_id" — fails RawAssessmentRow validation
                    "Rating": "Green",
                    "Comments": "no id",
                }
            ],
            "Summary": {
                "Interpretation": "Strong alignment",
                "Overall_Comments": "ok",
            },
        }
    }
    client: MagicMock = MagicMock()
    client.messages.create = AsyncMock(
        return_value=_make_response(json.dumps(bad_payload))
    )
    agent: SecurityAgent = _make_agent(client)

    with pytest.raises(ValidationError):
        await agent.assess(_DOCUMENT, _QUESTIONS)

    assert client.messages.create.await_count == 1
