import asyncio
import sys
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_EVAL_ROOT = Path(__file__).resolve().parent.parent / "app" / "agents" / "evaluation"
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from src.agents.schemas import AgentResult, AssessmentRow, PolicyDocResult, Summary  # noqa: E402

from app.core.enums import DocumentStatus  # noqa: E402
from app.orchestrator.main import _process_document, app  # noqa: E402
from app.orchestrator.session import DocumentSession  # noqa: E402

DOC_ID = "aaaaaaaa-0000-0000-0000-000000000001"
S3_KEY = f"{DOC_ID}_test.docx"
TEMPLATE = "SDA"
AGENT_TYPE = "general"
TASK_ID = f"{DOC_ID}_{AGENT_TYPE}"


def _make_agent_result() -> AgentResult:
    return AgentResult(
        agent_type=AGENT_TYPE,
        docs=[
            PolicyDocResult(
                policy_doc_filename="policy.pdf",
                policy_doc_url="https://example.com/policy.pdf",
                assessments=[
                    AssessmentRow(
                        Question="Is MFA enabled?",
                        Rating="Green",
                        Comments="MFA is enabled.",
                        Reference="C1.a",
                    )
                ],
                summary=Summary(
                    Interpretation="Satisfactory",
                    Overall_Comments="No issues found.",
                ),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.update_status = AsyncMock()
    return repo


def _make_session(
    expected_task_ids: set,
    collected_results: dict | None = None,
    *,
    event_set: bool = False,
) -> DocumentSession:
    session = DocumentSession(
        doc_id=DOC_ID,
        template_type=TEMPLATE,
        s3_key=S3_KEY,
        expected_task_ids=expected_task_ids,
        collected_results=dict(collected_results or {}),
    )
    if event_set:
        session.completion_event.set()
    return session


# ---------------------------------------------------------------------------
# POST /orchestrate endpoint
# ---------------------------------------------------------------------------


class TestOrchestrateEndpoint:
    def test_returns_202_accepted(self):
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.orchestrator.main._process_document", new=AsyncMock()):
            response = client.post(
                "/orchestrate",
                json={
                    "document_id": DOC_ID,
                    "s3_key": S3_KEY,
                    "template_type": TEMPLATE,
                },
            )
        assert response.status_code == 202

    def test_returns_accepted_body(self):
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.orchestrator.main._process_document", new=AsyncMock()):
            response = client.post(
                "/orchestrate",
                json={
                    "document_id": DOC_ID,
                    "s3_key": S3_KEY,
                    "template_type": TEMPLATE,
                },
            )
        assert response.json() == {"status": "accepted"}

    def test_missing_document_id_returns_422(self):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/orchestrate",
            json={"s3_key": S3_KEY, "template_type": TEMPLATE},
        )
        assert response.status_code == 422

    def test_missing_s3_key_returns_422(self):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/orchestrate",
            json={"document_id": DOC_ID, "template_type": TEMPLATE},
        )
        assert response.status_code == 422

    def test_missing_template_type_returns_422(self):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/orchestrate",
            json={"document_id": DOC_ID, "s3_key": S3_KEY},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# _process_document — COMPLETE (all agents respond in time)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_document_complete_writes_complete_status():
    """All expected results arrive before timeout → COMPLETE with result_md."""
    session = _make_session(
        expected_task_ids={TASK_ID},
        collected_results={TASK_ID: _make_agent_result()},
        event_set=True,
    )
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService") as mock_sqs_cls,
        patch("app.orchestrator.main._extract_text", return_value="content"),
        patch("app.orchestrator.main._session_store") as mock_store,
        patch("app.orchestrator.main.PipelineConfig") as mock_pipeline_cfg,
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"docx-bytes")
        mock_sqs_cls.return_value.send_task = AsyncMock()
        mock_store.create = AsyncMock(return_value=session)
        mock_store.remove = AsyncMock()
        mock_pipeline_cfg.return_value.section_labels = {}
        mock_pipeline_cfg.return_value.agent_types = [AGENT_TYPE]
        mock_pipeline_cfg.return_value.max_priority_actions = 10

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    mock_repo.update_status.assert_called_with(
        DOC_ID, DocumentStatus.COMPLETE.value, result_md=ANY
    )


@pytest.mark.asyncio
async def test_process_document_complete_result_md_contains_document_title():
    session = _make_session(
        expected_task_ids={TASK_ID},
        collected_results={TASK_ID: _make_agent_result()},
        event_set=True,
    )
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService") as mock_sqs_cls,
        patch("app.orchestrator.main._extract_text", return_value="text"),
        patch("app.orchestrator.main._session_store") as mock_store,
        patch("app.orchestrator.main.PipelineConfig") as mock_pipeline_cfg,
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"docx-bytes")
        mock_sqs_cls.return_value.send_task = AsyncMock()
        mock_store.create = AsyncMock(return_value=session)
        mock_store.remove = AsyncMock()
        mock_pipeline_cfg.return_value.section_labels = {}
        mock_pipeline_cfg.return_value.agent_types = [AGENT_TYPE]
        mock_pipeline_cfg.return_value.max_priority_actions = 10

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    _args, _kwargs = mock_repo.update_status.call_args
    result_md = _kwargs.get("result_md") or (_args[2] if len(_args) > 2 else None)
    assert result_md is not None
    # document_title strips the doc_id prefix from the s3_key filename
    expected_title = Path(S3_KEY).name.removeprefix(f"{DOC_ID}_")
    assert expected_title in result_md


# ---------------------------------------------------------------------------
# _process_document — ERROR (timeout, zero results)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_document_timeout_zero_results_writes_error():
    """Timeout fires with no collected results → ERROR."""
    session = _make_session(expected_task_ids={TASK_ID}, collected_results={}, event_set=False)
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService") as mock_sqs_cls,
        patch("app.orchestrator.main._extract_text", return_value="text"),
        patch("app.orchestrator.main._session_store") as mock_store,
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"docx-bytes")
        mock_sqs_cls.return_value.send_task = AsyncMock()
        mock_store.create = AsyncMock(return_value=session)
        mock_store.remove = AsyncMock()

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    mock_repo.update_status.assert_called_with(
        DOC_ID,
        DocumentStatus.ERROR.value,
        error_message="No agent responses received within timeout.",
    )


@pytest.mark.asyncio
async def test_process_document_timeout_zero_results_does_not_produce_result_md():
    session = _make_session(expected_task_ids={TASK_ID}, collected_results={}, event_set=False)
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService") as mock_sqs_cls,
        patch("app.orchestrator.main._extract_text", return_value="text"),
        patch("app.orchestrator.main._session_store") as mock_store,
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"docx-bytes")
        mock_sqs_cls.return_value.send_task = AsyncMock()
        mock_store.create = AsyncMock(return_value=session)
        mock_store.remove = AsyncMock()

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    call_kwargs = mock_repo.update_status.call_args.kwargs
    assert "result_md" not in call_kwargs or call_kwargs.get("result_md") is None


# ---------------------------------------------------------------------------
# _process_document — PARTIAL_COMPLETE (timeout, some results arrived)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_document_timeout_partial_results_writes_partial_complete():
    """Timeout fires with ≥1 result → PARTIAL_COMPLETE."""
    task_b = f"{DOC_ID}_risk"
    session = _make_session(
        expected_task_ids={TASK_ID, task_b},
        collected_results={TASK_ID: _make_agent_result()},
        event_set=False,
    )
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService") as mock_sqs_cls,
        patch("app.orchestrator.main._extract_text", return_value="text"),
        patch("app.orchestrator.main._session_store") as mock_store,
        patch("app.orchestrator.main.PipelineConfig") as mock_pipeline_cfg,
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"docx-bytes")
        mock_sqs_cls.return_value.send_task = AsyncMock()
        mock_store.create = AsyncMock(return_value=session)
        mock_store.remove = AsyncMock()
        mock_pipeline_cfg.return_value.section_labels = {}
        mock_pipeline_cfg.return_value.agent_types = [AGENT_TYPE]
        mock_pipeline_cfg.return_value.max_priority_actions = 10

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    call_args = mock_repo.update_status.call_args
    assert call_args.args[1] == DocumentStatus.PARTIAL_COMPLETE.value


@pytest.mark.asyncio
async def test_process_document_partial_complete_includes_missing_agents_in_error_message():
    task_b = f"{DOC_ID}_risk"
    session = _make_session(
        expected_task_ids={TASK_ID, task_b},
        collected_results={TASK_ID: _make_agent_result()},
        event_set=False,
    )
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService") as mock_sqs_cls,
        patch("app.orchestrator.main._extract_text", return_value="text"),
        patch("app.orchestrator.main._session_store") as mock_store,
        patch("app.orchestrator.main.PipelineConfig") as mock_pipeline_cfg,
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"docx-bytes")
        mock_sqs_cls.return_value.send_task = AsyncMock()
        mock_store.create = AsyncMock(return_value=session)
        mock_store.remove = AsyncMock()
        mock_pipeline_cfg.return_value.section_labels = {}
        mock_pipeline_cfg.return_value.agent_types = [AGENT_TYPE]
        mock_pipeline_cfg.return_value.max_priority_actions = 10

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    call_kwargs = mock_repo.update_status.call_args.kwargs
    error_msg = call_kwargs.get("error_message", "")
    assert "risk" in error_msg


@pytest.mark.asyncio
async def test_process_document_partial_complete_includes_result_md():
    task_b = f"{DOC_ID}_risk"
    session = _make_session(
        expected_task_ids={TASK_ID, task_b},
        collected_results={TASK_ID: _make_agent_result()},
        event_set=False,
    )
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService") as mock_sqs_cls,
        patch("app.orchestrator.main._extract_text", return_value="text"),
        patch("app.orchestrator.main._session_store") as mock_store,
        patch("app.orchestrator.main.PipelineConfig") as mock_pipeline_cfg,
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"docx-bytes")
        mock_sqs_cls.return_value.send_task = AsyncMock()
        mock_store.create = AsyncMock(return_value=session)
        mock_store.remove = AsyncMock()
        mock_pipeline_cfg.return_value.section_labels = {}
        mock_pipeline_cfg.return_value.agent_types = [AGENT_TYPE]
        mock_pipeline_cfg.return_value.max_priority_actions = 10

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    call_kwargs = mock_repo.update_status.call_args.kwargs
    assert call_kwargs.get("result_md") is not None


# ---------------------------------------------------------------------------
# _process_document — ERROR (S3 download failure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_document_s3_failure_writes_error_status():
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService"),
        patch("app.orchestrator.main._session_store"),
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(
            side_effect=RuntimeError("S3 connection refused")
        )

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    last_call = mock_repo.update_status.call_args
    assert last_call.args[1] == DocumentStatus.ERROR.value


@pytest.mark.asyncio
async def test_process_document_s3_failure_includes_exception_in_error_message():
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService"),
        patch("app.orchestrator.main._session_store"),
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(
            side_effect=RuntimeError("S3 connection refused")
        )

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    call_kwargs = mock_repo.update_status.call_args.kwargs
    assert "S3 connection refused" in call_kwargs.get("error_message", "")


# ---------------------------------------------------------------------------
# _process_document — ERROR (text extraction failure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_document_extraction_failure_writes_error_status():
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService"),
        patch(
            "app.orchestrator.main._extract_text",
            side_effect=ValueError("DOCX file contains no extractable text content."),
        ),
        patch("app.orchestrator.main._session_store"),
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"corrupt-bytes")

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    last_call = mock_repo.update_status.call_args
    assert last_call.args[1] == DocumentStatus.ERROR.value


@pytest.mark.asyncio
async def test_process_document_extraction_failure_includes_message():
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService"),
        patch(
            "app.orchestrator.main._extract_text",
            side_effect=ValueError("DOCX file contains no extractable text content."),
        ),
        patch("app.orchestrator.main._session_store"),
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"bad")

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    call_kwargs = mock_repo.update_status.call_args.kwargs
    assert "no extractable text" in call_kwargs.get("error_message", "")


# ---------------------------------------------------------------------------
# _process_document — session is always cleaned up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_document_removes_session_after_complete():
    session = _make_session(
        expected_task_ids={TASK_ID},
        collected_results={TASK_ID: _make_agent_result()},
        event_set=True,
    )
    mock_repo = _make_mock_repo()

    with (
        patch("app.orchestrator.main.get_postgres_pool", new=AsyncMock(return_value=MagicMock())),
        patch("app.orchestrator.main.AppContext"),
        patch("app.orchestrator.main.DocumentRepository", return_value=mock_repo),
        patch("app.orchestrator.main.S3Service") as mock_s3_cls,
        patch("app.orchestrator.main.SQSService") as mock_sqs_cls,
        patch("app.orchestrator.main._extract_text", return_value="text"),
        patch("app.orchestrator.main._session_store") as mock_store,
        patch("app.orchestrator.main.PipelineConfig") as mock_pipeline_cfg,
    ):
        mock_s3_cls.return_value.download_file = AsyncMock(return_value=b"docx-bytes")
        mock_sqs_cls.return_value.send_task = AsyncMock()
        mock_store.create = AsyncMock(return_value=session)
        mock_store.remove = AsyncMock()
        mock_pipeline_cfg.return_value.section_labels = {}
        mock_pipeline_cfg.return_value.agent_types = [AGENT_TYPE]
        mock_pipeline_cfg.return_value.max_priority_actions = 10

        await _process_document(DOC_ID, S3_KEY, TEMPLATE)

    mock_store.remove.assert_called_once_with(DOC_ID)
