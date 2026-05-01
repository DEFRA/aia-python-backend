"""Tests for app.relay_service.worker — dispatch and polling loop."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.status_message import StatusMessage
from app.models.task_message import TaskMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides: Any) -> TaskMessage:
    defaults: dict[str, Any] = dict(
        task_id="doc1_security",
        document_id="doc1",
        agent_type="security",
        template_type="SDA",
        file_content="Policy document text.",
        s3_bucket="docsupload",
        s3_key="uploads/doc1.docx",
    )
    defaults.update(overrides)
    return TaskMessage(**defaults)


def _make_agent_result() -> Any:
    """Build a minimal AgentResult using the evaluation module schemas."""
    from src.agents.schemas import (  # noqa: PLC0415 — evaluation path injected at import
        AgentResult,
        AssessmentRow,
        FinalSummary,
        LLMResponseMeta,
        Reference,
    )

    return AgentResult(
        assessments=[
            AssessmentRow(
                Question="Is MFA enabled?",
                Rating="Green",
                Comments="MFA is enabled for all users.",
                Reference=Reference(text="C1.a", url="https://example.com"),
            )
        ],
        metadata=LLMResponseMeta(
            model="claude-3-5-haiku-latest",
            input_tokens=100,
            output_tokens=50,
            stop_reason="end_turn",
        ),
        final_summary=FinalSummary(
            Interpretation="The document is compliant.",
            Overall_Comments="No issues found.",
        ),
    )


# ---------------------------------------------------------------------------
# _get_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_returns_inline_content() -> None:
    from app.relay_service.worker import _get_document

    task = _make_task(file_content="Inline text")
    s3 = AsyncMock()
    doc = await _get_document(task, s3)
    assert doc == "Inline text"
    s3.download_file.assert_not_called()


@pytest.mark.asyncio
async def test_get_document_downloads_from_s3_when_no_inline() -> None:
    from app.relay_service.worker import _get_document

    task = _make_task(file_content=None)
    s3 = AsyncMock()
    s3.download_file.return_value = b"S3 document text"
    doc = await _get_document(task, s3)
    assert doc == "S3 document text"
    s3.download_file.assert_called_once_with(task.s3_key, bucket=task.s3_bucket)


# ---------------------------------------------------------------------------
# dispatch — unknown agent type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_returns_error_for_unknown_agent_type() -> None:
    from app.relay_service.worker import dispatch

    task = _make_task(agent_type="does_not_exist")
    s3 = AsyncMock()
    status = await dispatch(task, s3)
    assert status.task_id == task.task_id
    assert status.agent_type == "does_not_exist"
    assert status.error is not None
    assert "does_not_exist" in status.error
    assert status.result == {}


# ---------------------------------------------------------------------------
# dispatch — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_returns_populated_status_on_success() -> None:
    from app.relay_service.worker import dispatch

    task = _make_task()
    s3 = AsyncMock()
    result = _make_agent_result()
    mock_agent = AsyncMock()
    mock_agent.assess.return_value = result

    with (
        patch("app.relay_service.worker._get_db_config") as mock_db_cfg,
        patch(
            "app.relay_service.worker.fetch_assessment_by_category",
            new=AsyncMock(return_value=([], "https://example.com")),
        ),
        patch("app.relay_service.worker.make_llm_client", return_value=MagicMock()),
        patch(
            "app.relay_service.worker.AGENT_REGISTRY",
            {"security": lambda **_: mock_agent},
        ),
        patch(
            "app.relay_service.worker.CONFIG_REGISTRY",
            {"security": MagicMock(return_value=MagicMock())},
        ),
    ):
        mock_db_cfg.return_value = MagicMock(dsn="postgresql://test:test@localhost/test")
        status = await dispatch(task, s3)

    assert status.task_id == task.task_id
    assert status.document_id == task.document_id
    assert status.agent_type == "security"
    assert status.error is None
    assert "assessments" in status.result


# ---------------------------------------------------------------------------
# dispatch — agent failure propagated as StatusMessage.error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_captures_agent_exception_as_error() -> None:
    from app.relay_service.worker import dispatch

    task = _make_task()
    s3 = AsyncMock()
    mock_agent = AsyncMock()
    mock_agent.assess.side_effect = ValueError("LLM parse error")

    with (
        patch("app.relay_service.worker._get_db_config") as mock_db_cfg,
        patch(
            "app.relay_service.worker.fetch_assessment_by_category",
            new=AsyncMock(return_value=([], "https://example.com")),
        ),
        patch("app.relay_service.worker.make_llm_client", return_value=MagicMock()),
        patch(
            "app.relay_service.worker.AGENT_REGISTRY",
            {"security": lambda **_: mock_agent},
        ),
        patch(
            "app.relay_service.worker.CONFIG_REGISTRY",
            {"security": MagicMock(return_value=MagicMock())},
        ),
    ):
        mock_db_cfg.return_value = MagicMock(dsn="postgresql://test:test@localhost/test")
        status = await dispatch(task, s3)

    assert status.error == "LLM parse error"
    assert status.result == {}


# ---------------------------------------------------------------------------
# run_worker — single iteration smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_worker_processes_one_message_then_cancels() -> None:
    """Worker receives one message, dispatches, publishes, deletes, then is cancelled."""
    from app.relay_service.worker import run_worker

    task = _make_task()
    raw_msg = {"body": task.model_dump_json(by_alias=True), "receipt_handle": "rh-abc"}

    mock_sqs = AsyncMock()
    # First call returns one message; subsequent calls block until cancelled
    mock_sqs.receive_messages.side_effect = [
        [raw_msg],
        asyncio.CancelledError(),
    ]

    mock_status = StatusMessage(
        task_id=task.task_id,
        document_id=task.document_id,
        agent_type="security",
        result={"assessments": []},
    )

    with (
        patch("app.relay_service.worker.SQSService", return_value=mock_sqs),
        patch("app.relay_service.worker.S3Service", return_value=AsyncMock()),
        patch(
            "app.relay_service.worker.dispatch",
            new=AsyncMock(return_value=mock_status),
        ),
        patch("app.relay_service.worker.app_config") as mock_cfg,
    ):
        mock_cfg.sqs.task_queue_url = "http://localhost/tasks"
        mock_cfg.sqs.status_queue_url = "http://localhost/status"

        await run_worker()

    mock_sqs.publish.assert_called_once()
    published_body = json.loads(mock_sqs.publish.call_args[0][1])
    assert published_body["taskId"] == task.task_id

    mock_sqs.delete_message.assert_called_once_with("http://localhost/tasks", "rh-abc")
