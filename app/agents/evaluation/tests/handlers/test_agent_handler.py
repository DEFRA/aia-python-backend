"""Tests for the Stage 6 Specialist Agent Lambda handler.

Covers:
- Dispatch to the correct agent class based on agentType
- S3 pointer resolution for large documents
- Success status message published to SQS Status queue
- Failure status message published on agent exception
- CloudWatch metrics emission (duration, success, failure)
- Unknown agent type raises ValueError
- AgentTaskBody schema enforces typed questions + required categoryUrl
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.agents.schemas import (
    AgentResult,
    AssessmentRow,
    FinalSummary,
    LLMResponseMeta,
    QuestionItem,
    Reference,
)
from src.handlers.agent import AGENT_REGISTRY, AgentSqsEvent, AgentTaskBody, _handler

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_SAMPLE_RESULT: AgentResult = AgentResult(
    assessments=[
        AssessmentRow(
            Question="Q1",
            Rating="Green",
            Comments="Found in Section 1.",
            Reference=Reference(text="Ref-1", url="https://example.test/policy"),
        ),
    ],
    metadata=LLMResponseMeta(
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        stop_reason="end_turn",
    ),
    final_summary=FinalSummary(
        Interpretation="Strong alignment",
        Overall_Comments="All good.",
    ),
)

_DEFAULT_QUESTIONS: list[dict[str, str]] = [
    {"question": "Is auth defined?", "reference": "Ref-1"},
    {"question": "Is encryption used?", "reference": "Ref-2"},
]

_DEFAULT_CATEGORY_URL: str = "https://example.test/category"


def _build_sqs_event(
    agent_type: str = "security",
    document: str | None = "Test document text",
    s3_payload_key: str | None = None,
) -> dict[str, Any]:
    """Build a minimal SQS Lambda event dict for testing."""
    body: dict[str, Any] = {
        "docId": "doc-001",
        "agentType": agent_type,
        "questions": _DEFAULT_QUESTIONS,
        "categoryUrl": _DEFAULT_CATEGORY_URL,
        "enqueuedAt": "2026-04-14T10:00:00Z",
    }
    if document is not None:
        body["document"] = document
    if s3_payload_key is not None:
        body["s3PayloadKey"] = s3_payload_key

    return {"Records": [{"body": json.dumps(body)}]}


def _make_mock_agent(
    return_value: AgentResult | None = None,
    side_effect: Exception | None = None,
) -> MagicMock:
    """Build a mock agent class whose instances have a mocked assess() method."""
    mock_instance: MagicMock = MagicMock()
    if side_effect is not None:
        mock_instance.assess = AsyncMock(side_effect=side_effect)
    else:
        mock_instance.assess = AsyncMock(return_value=return_value or _SAMPLE_RESULT)

    mock_cls: MagicMock = MagicMock(return_value=mock_instance)
    return mock_cls


# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


def test_agent_sqs_event_validates_correctly() -> None:
    """AgentSqsEvent should parse a valid SQS event dict."""
    event: dict[str, Any] = _build_sqs_event()
    parsed: AgentSqsEvent = AgentSqsEvent.model_validate(event)
    assert len(parsed.Records) == 1


def test_agent_task_body_validates_typed_questions() -> None:
    """AgentTaskBody should parse questions into typed ``QuestionItem`` instances."""
    body_dict: dict[str, Any] = {
        "docId": "doc-001",
        "agentType": "data",
        "document": "Some text",
        "questions": _DEFAULT_QUESTIONS,
        "categoryUrl": _DEFAULT_CATEGORY_URL,
        "enqueuedAt": "2026-04-14T10:00:00Z",
    }
    body: AgentTaskBody = AgentTaskBody.model_validate(body_dict)
    assert body.docId == "doc-001"
    assert body.agentType == "data"
    assert body.document == "Some text"
    assert body.s3PayloadKey is None
    assert body.categoryUrl == _DEFAULT_CATEGORY_URL
    assert all(isinstance(q, QuestionItem) for q in body.questions)
    assert body.questions[0].question == "Is auth defined?"
    assert body.questions[0].reference == "Ref-1"


def test_agent_task_body_allows_s3_pointer() -> None:
    """AgentTaskBody should accept s3PayloadKey without inline document."""
    body_dict: dict[str, Any] = {
        "docId": "doc-002",
        "agentType": "risk",
        "s3PayloadKey": "payloads/doc-002.txt",
        "questions": _DEFAULT_QUESTIONS,
        "categoryUrl": _DEFAULT_CATEGORY_URL,
        "enqueuedAt": "2026-04-14T10:00:00Z",
    }
    body: AgentTaskBody = AgentTaskBody.model_validate(body_dict)
    assert body.s3PayloadKey == "payloads/doc-002.txt"
    assert body.document is None


def test_agent_task_body_rejects_legacy_string_questions() -> None:
    """A list of bare strings must fail validation under the new schema."""
    body_dict: dict[str, Any] = {
        "docId": "doc-003",
        "agentType": "security",
        "document": "x",
        "questions": ["Is MFA enforced?", "Is encryption applied?"],
        "categoryUrl": _DEFAULT_CATEGORY_URL,
        "enqueuedAt": "2026-04-14T10:00:00Z",
    }
    with pytest.raises(ValidationError):
        AgentTaskBody.model_validate(body_dict)


def test_agent_task_body_requires_category_url() -> None:
    """``categoryUrl`` is required and missing it must raise ``ValidationError``."""
    body_dict: dict[str, Any] = {
        "docId": "doc-004",
        "agentType": "security",
        "document": "x",
        "questions": _DEFAULT_QUESTIONS,
        "enqueuedAt": "2026-04-14T10:00:00Z",
    }
    with pytest.raises(ValidationError):
        AgentTaskBody.model_validate(body_dict)


# ---------------------------------------------------------------------------
# Handler dispatch tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_dispatches_to_security_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handler should instantiate SecurityAgent for agentType='security'."""
    monkeypatch.setenv("SQS_STATUS_QUEUE_URL", "https://sqs.example.com/status")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    sent_messages: list[dict[str, Any]] = []
    mock_agent_cls: MagicMock = _make_mock_agent()

    async def mock_send(sqs_client: Any, queue_url: str, message_body: dict[str, Any]) -> None:
        sent_messages.append(message_body)

    async def mock_emit(
        name: str, value: float, unit: str = "Milliseconds", agent_type: str = ""
    ) -> None:
        pass

    with (
        patch("src.handlers.agent._send_status_message", side_effect=mock_send),
        patch("src.handlers.agent._emit_metric", side_effect=mock_emit),
        patch("src.handlers.agent._get_sqs", return_value=MagicMock()),
        patch("src.handlers.agent._get_cw", return_value=MagicMock()),
        patch.dict(AGENT_REGISTRY, {"security": mock_agent_cls}),
        patch("src.handlers.agent.anthropic") as mock_anthropic_mod,
    ):
        mock_anthropic_mod.AsyncAnthropic.return_value = MagicMock()

        event: dict[str, Any] = _build_sqs_event(agent_type="security")
        result: dict[str, Any] = await _handler(event, {})

    assert result == {"statusCode": 200}
    assert len(sent_messages) == 1
    assert sent_messages[0]["status"] == "completed"
    assert sent_messages[0]["agentType"] == "security"
    assert sent_messages[0]["docId"] == "doc-001"
    assert "result" in sent_messages[0]
    mock_agent_cls.assert_called_once()

    # Verify the agent instance was called with typed QuestionItem objects and category_url
    mock_instance = mock_agent_cls.return_value
    mock_instance.assess.assert_awaited_once()
    call_kwargs = mock_instance.assess.await_args.kwargs
    assert call_kwargs["category_url"] == _DEFAULT_CATEGORY_URL
    assert all(isinstance(q, QuestionItem) for q in call_kwargs["questions"])


@pytest.mark.asyncio
async def test_handler_dispatches_to_data_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handler should instantiate DataAgent for agentType='data'."""
    monkeypatch.setenv("SQS_STATUS_QUEUE_URL", "https://sqs.example.com/status")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    sent_messages: list[dict[str, Any]] = []
    mock_agent_cls: MagicMock = _make_mock_agent()

    async def mock_send(sqs_client: Any, queue_url: str, message_body: dict[str, Any]) -> None:
        sent_messages.append(message_body)

    async def mock_emit(
        name: str, value: float, unit: str = "Milliseconds", agent_type: str = ""
    ) -> None:
        pass

    with (
        patch("src.handlers.agent._send_status_message", side_effect=mock_send),
        patch("src.handlers.agent._emit_metric", side_effect=mock_emit),
        patch("src.handlers.agent._get_sqs", return_value=MagicMock()),
        patch("src.handlers.agent._get_cw", return_value=MagicMock()),
        patch.dict(AGENT_REGISTRY, {"data": mock_agent_cls}),
        patch("src.handlers.agent.anthropic") as mock_anthropic_mod,
    ):
        mock_anthropic_mod.AsyncAnthropic.return_value = MagicMock()

        event: dict[str, Any] = _build_sqs_event(agent_type="data")
        result: dict[str, Any] = await _handler(event, {})

    assert result == {"statusCode": 200}
    assert len(sent_messages) == 1
    assert sent_messages[0]["status"] == "completed"
    assert sent_messages[0]["agentType"] == "data"


# ---------------------------------------------------------------------------
# S3 pointer resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_resolves_s3_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handler should download document from S3 when s3PayloadKey is provided."""
    monkeypatch.setenv("SQS_STATUS_QUEUE_URL", "https://sqs.example.com/status")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    downloaded_keys: list[str] = []

    async def mock_download(s3_client: Any, bucket: str, key: str) -> str:
        downloaded_keys.append(key)
        return "Document from S3"

    sent_messages: list[dict[str, Any]] = []

    async def mock_send(sqs_client: Any, queue_url: str, message_body: dict[str, Any]) -> None:
        sent_messages.append(message_body)

    async def mock_emit(
        name: str, value: float, unit: str = "Milliseconds", agent_type: str = ""
    ) -> None:
        pass

    mock_agent_cls: MagicMock = _make_mock_agent()

    with (
        patch("src.handlers.agent._download_s3_payload", side_effect=mock_download),
        patch("src.handlers.agent._send_status_message", side_effect=mock_send),
        patch("src.handlers.agent._emit_metric", side_effect=mock_emit),
        patch("src.handlers.agent._get_sqs", return_value=MagicMock()),
        patch("src.handlers.agent._get_s3", return_value=MagicMock()),
        patch("src.handlers.agent._get_cw", return_value=MagicMock()),
        patch.dict(AGENT_REGISTRY, {"security": mock_agent_cls}),
        patch("src.handlers.agent.anthropic") as mock_anthropic_mod,
    ):
        mock_anthropic_mod.AsyncAnthropic.return_value = MagicMock()

        event: dict[str, Any] = _build_sqs_event(
            agent_type="security",
            document=None,
            s3_payload_key="payloads/doc-001.txt",
        )
        await _handler(event, {})

    assert downloaded_keys == ["payloads/doc-001.txt"]
    assert sent_messages[0]["status"] == "completed"


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_publishes_failure_on_agent_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handler should catch agent exceptions and publish failure status."""
    monkeypatch.setenv("SQS_STATUS_QUEUE_URL", "https://sqs.example.com/status")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    sent_messages: list[dict[str, Any]] = []
    mock_agent_cls: MagicMock = _make_mock_agent(
        side_effect=ValueError("Claude returned garbage"),
    )

    async def mock_send(sqs_client: Any, queue_url: str, message_body: dict[str, Any]) -> None:
        sent_messages.append(message_body)

    async def mock_emit(
        name: str, value: float, unit: str = "Milliseconds", agent_type: str = ""
    ) -> None:
        pass

    with (
        patch("src.handlers.agent._send_status_message", side_effect=mock_send),
        patch("src.handlers.agent._emit_metric", side_effect=mock_emit),
        patch("src.handlers.agent._get_sqs", return_value=MagicMock()),
        patch("src.handlers.agent._get_cw", return_value=MagicMock()),
        patch.dict(AGENT_REGISTRY, {"security": mock_agent_cls}),
        patch("src.handlers.agent.anthropic") as mock_anthropic_mod,
    ):
        mock_anthropic_mod.AsyncAnthropic.return_value = MagicMock()

        event: dict[str, Any] = _build_sqs_event(agent_type="security")
        result: dict[str, Any] = await _handler(event, {})

    assert result == {"statusCode": 200}
    assert len(sent_messages) == 1
    assert sent_messages[0]["status"] == "failed"
    assert sent_messages[0]["agentType"] == "security"
    assert "Claude returned garbage" in sent_messages[0]["errorMessage"]


# ---------------------------------------------------------------------------
# Metrics emission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_emits_success_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handler should emit AgentDuration and AgentSuccess metrics on success."""
    monkeypatch.setenv("SQS_STATUS_QUEUE_URL", "https://sqs.example.com/status")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    emitted_metrics: list[dict[str, Any]] = []
    mock_agent_cls: MagicMock = _make_mock_agent()

    async def mock_send(sqs_client: Any, queue_url: str, message_body: dict[str, Any]) -> None:
        pass

    async def mock_emit(
        name: str, value: float, unit: str = "Milliseconds", agent_type: str = ""
    ) -> None:
        emitted_metrics.append(
            {"name": name, "value": value, "unit": unit, "agent_type": agent_type}
        )

    with (
        patch("src.handlers.agent._send_status_message", side_effect=mock_send),
        patch("src.handlers.agent._emit_metric", side_effect=mock_emit),
        patch("src.handlers.agent._get_sqs", return_value=MagicMock()),
        patch("src.handlers.agent._get_cw", return_value=MagicMock()),
        patch.dict(AGENT_REGISTRY, {"security": mock_agent_cls}),
        patch("src.handlers.agent.anthropic") as mock_anthropic_mod,
    ):
        mock_anthropic_mod.AsyncAnthropic.return_value = MagicMock()

        event: dict[str, Any] = _build_sqs_event(agent_type="security")
        await _handler(event, {})

    metric_names: list[str] = [m["name"] for m in emitted_metrics]
    assert "AgentDuration" in metric_names
    assert "AgentSuccess" in metric_names

    for metric in emitted_metrics:
        assert metric["agent_type"] == "security"


@pytest.mark.asyncio
async def test_handler_emits_failure_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handler should emit AgentDuration and AgentFailure metrics on failure."""
    monkeypatch.setenv("SQS_STATUS_QUEUE_URL", "https://sqs.example.com/status")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    emitted_metrics: list[dict[str, Any]] = []
    mock_agent_cls: MagicMock = _make_mock_agent(side_effect=RuntimeError("boom"))

    async def mock_send(sqs_client: Any, queue_url: str, message_body: dict[str, Any]) -> None:
        pass

    async def mock_emit(
        name: str, value: float, unit: str = "Milliseconds", agent_type: str = ""
    ) -> None:
        emitted_metrics.append(
            {"name": name, "value": value, "unit": unit, "agent_type": agent_type}
        )

    with (
        patch("src.handlers.agent._send_status_message", side_effect=mock_send),
        patch("src.handlers.agent._emit_metric", side_effect=mock_emit),
        patch("src.handlers.agent._get_sqs", return_value=MagicMock()),
        patch("src.handlers.agent._get_cw", return_value=MagicMock()),
        patch.dict(AGENT_REGISTRY, {"security": mock_agent_cls}),
        patch("src.handlers.agent.anthropic") as mock_anthropic_mod,
    ):
        mock_anthropic_mod.AsyncAnthropic.return_value = MagicMock()

        event: dict[str, Any] = _build_sqs_event(agent_type="security")
        await _handler(event, {})

    metric_names: list[str] = [m["name"] for m in emitted_metrics]
    assert "AgentDuration" in metric_names
    assert "AgentFailure" in metric_names


# ---------------------------------------------------------------------------
# Unknown agent type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_raises_on_unknown_agent_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handler should raise ValueError for an unrecognised agent type."""
    monkeypatch.setenv("SQS_STATUS_QUEUE_URL", "https://sqs.example.com/status")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    event: dict[str, Any] = _build_sqs_event(agent_type="unknown_agent")

    with pytest.raises(ValueError, match="Unknown agent type"):
        await _handler(event, {})
