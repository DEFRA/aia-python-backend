"""Tests for app.relay_service.worker — dispatch and polling loop."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_EVAL_ROOT = Path(__file__).resolve().parent.parent / "app" / "agents" / "evaluation"
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from src.agents.schemas import AgentLLMOutput, QuestionItem, RawAssessmentRow, Summary  # noqa: E402

from app.models.status_message import StatusMessage  # noqa: E402
from app.models.task_message import TaskMessage  # noqa: E402

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


def _make_llm_output() -> AgentLLMOutput:
    return AgentLLMOutput(
        rows=[
            RawAssessmentRow(
                question_id="q-001",
                Rating="Green",
                Comments="MFA is enabled for all users.",
            )
        ],
        summary=Summary(
            Interpretation="The document is compliant.",
            Overall_Comments="No issues found.",
        ),
    )


def _make_questions() -> list[QuestionItem]:
    return [
        QuestionItem(
            id="q-001",
            question="Is MFA enabled?",
            reference="C1.a",
        )
    ]


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


@pytest.mark.asyncio
async def test_get_document_raises_when_no_content_and_no_s3_fields() -> None:
    from app.relay_service.worker import _get_document

    task = _make_task(file_content=None, s3_key=None, s3_bucket=None)
    s3 = AsyncMock()
    with pytest.raises(ValueError, match="no s3_key/s3_bucket"):
        await _get_document(task, s3)


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
    llm_output = _make_llm_output()
    questions = _make_questions()
    mock_agent = AsyncMock()
    mock_agent.assess.return_value = llm_output

    with (
        patch("app.relay_service.worker._get_db_config") as mock_db_cfg,
        patch(
            "app.relay_service.worker.fetch_policy_doc_by_category",
            new=AsyncMock(return_value=("policy-doc-id-001", "https://example.com", "policy.pdf")),
        ),
        patch(
            "app.relay_service.worker.fetch_questions_by_policy_doc_id",
            new=AsyncMock(return_value=questions),
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
    questions = _make_questions()
    mock_agent = AsyncMock()
    mock_agent.assess.side_effect = ValueError("LLM parse error")

    with (
        patch("app.relay_service.worker._get_db_config") as mock_db_cfg,
        patch(
            "app.relay_service.worker.fetch_policy_doc_by_category",
            new=AsyncMock(return_value=("policy-doc-id-001", "https://example.com", "policy.pdf")),
        ),
        patch(
            "app.relay_service.worker.fetch_questions_by_policy_doc_id",
            new=AsyncMock(return_value=questions),
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
# dispatch — LLM timeout surfaced as StatusMessage.error (not a retry)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_returns_error_on_agent_timeout() -> None:
    """asyncio.TimeoutError from wait_for must produce an error StatusMessage,
    not propagate — the message must still be deleted, not retried."""
    from app.relay_service.worker import dispatch

    task = _make_task()
    s3 = AsyncMock()
    questions = _make_questions()
    mock_agent = AsyncMock()

    async def _slow_assess(**_: Any) -> None:
        await asyncio.sleep(9999)

    mock_agent.assess.side_effect = _slow_assess

    with (
        patch("app.relay_service.worker._get_db_config") as mock_db_cfg,
        patch(
            "app.relay_service.worker.fetch_policy_doc_by_category",
            new=AsyncMock(return_value=("policy-doc-id-001", "https://example.com", "policy.pdf")),
        ),
        patch(
            "app.relay_service.worker.fetch_questions_by_policy_doc_id",
            new=AsyncMock(return_value=questions),
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
        patch("app.relay_service.worker._AGENT_TIMEOUT_SECONDS", 1),
    ):
        mock_db_cfg.return_value = MagicMock(dsn="postgresql://test:test@localhost/test")
        status = await dispatch(task, s3)

    assert status.error is not None
    assert "timed out" in status.error.lower()
    assert "1s" in status.error
    assert status.result == {}


# ---------------------------------------------------------------------------
# _process_message — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_publishes_and_deletes_on_success() -> None:
    """_process_message publishes StatusMessage and deletes the SQS message on success."""
    from app.relay_service.worker import _process_message

    task = _make_task()
    raw_msg = {"body": task.model_dump_json(by_alias=True), "receipt_handle": "rh-abc"}

    mock_sqs = AsyncMock()
    mock_s3 = AsyncMock()
    semaphore = asyncio.Semaphore(10)

    mock_status = StatusMessage(
        task_id=task.task_id,
        document_id=task.document_id,
        agent_type="security",
        result={"assessments": []},
    )

    with patch(
        "app.relay_service.worker.dispatch",
        new=AsyncMock(return_value=mock_status),
    ):
        await _process_message(
            raw_msg, mock_sqs, mock_s3, "http://localhost/tasks", "http://localhost/status", semaphore
        )

    mock_sqs.publish.assert_called_once()
    published_body = json.loads(mock_sqs.publish.call_args[0][1])
    assert published_body["taskId"] == task.task_id
    mock_sqs.delete_message.assert_called_once_with("http://localhost/tasks", "rh-abc")


@pytest.mark.asyncio
async def test_process_message_does_not_delete_on_exception() -> None:
    """_process_message leaves message in-flight when an unexpected exception occurs."""
    from app.relay_service.worker import _process_message

    task = _make_task()
    raw_msg = {"body": task.model_dump_json(by_alias=True), "receipt_handle": "rh-xyz"}

    mock_sqs = AsyncMock()
    mock_s3 = AsyncMock()
    semaphore = asyncio.Semaphore(10)

    with patch(
        "app.relay_service.worker.dispatch",
        new=AsyncMock(side_effect=RuntimeError("infra failure")),
    ):
        await _process_message(
            raw_msg, mock_sqs, mock_s3, "http://localhost/tasks", "http://localhost/status", semaphore
        )

    mock_sqs.delete_message.assert_not_called()
