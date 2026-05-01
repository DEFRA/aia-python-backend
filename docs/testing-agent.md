## Disclaimer: "This document contains proprietary and confidential information belonging to DEFRA / AIA Programme. Unauthorised use, disclosure, or distribution is prohibited."

---
name: testing-agent
description: Establishes a comprehensive test strategy and automated tests for the AIA Backend — covering CoreBackend, Orchestrator, Relay Service, and DataPipeline modules.
version: 0.1
workspace: aia-backend
last-updated: 2026-05-01
---

## Purpose

Provide a safety net that enables confident, incremental development across all four service modules. Every critical flow must have test coverage before changes to that flow are merged.

---

## Module Map

| Module | Entry Point | Port | Key Concern |
|--------|-------------|------|-------------|
| CoreBackend | `app.api.main:app` | 8086 | REST API, auth, DB writes |
| Orchestrator | `app.orchestrator.main:app` | 8001 | Fan-out, session, summary, DB |
| Relay Service | `app.relay_service.main:app` | 8002 | SQS polling, LLM dispatch, status publish |
| DataPipeline | `app.datapipeline.src.main` | Lambda / CLI | SharePoint fetch, LLM question extraction, DB sync |

---

## Test Pyramid

```
          ┌────────────────────────┐
          │   E2E / Contract Tests  │  ← minimal; happy-path upload → result flow
          ├────────────────────────┤
          │  Integration Tests      │  ← DB, SQS, S3 (mocked or LocalStack)
          ├────────────────────────┤
          │  Unit Tests             │  ← pure logic, no I/O (dominant tier)
          └────────────────────────┘
```

**Coverage target:** 80 % line coverage across all modules.

---

## Module 1 — CoreBackend (`app/api/`)

### Critical Flows

| Flow | Test Type |
|------|-----------|
| `POST /api/v1/documents/upload` → 202 + documentId | Unit + Integration |
| Duplicate filename → 400 | Unit |
| Missing auth header → 401 | Unit |
| `x-user-id` / JWT sub mismatch → 403 | Unit |
| `GET /api/v1/documents/status` → processingDocumentIds | Unit + Integration |
| `GET /api/v1/documents?page=1&limit=20` → paginated history | Unit + Integration |
| `GET /api/v1/documents/{documentId}` → ResultRecord | Unit + Integration |
| `GET /api/v1/documents/{documentId}` unknown → 404 | Unit |
| `GET /api/v1/users/me` → guest user fallback | Unit |
| `GET /health` → `{ "status": "ok" }` | Unit |

### Existing Tests

- `tests/test_upload_router.py` — POST /upload (legacy path); **needs migration** to new `/api/v1/documents/upload` path and updated assertions for `202` + `documentId`.
- `tests/test_document_repository.py` — repository-level DB operations.
- `tests/test_sqs_service.py` — SQS send/receive/delete.

### Gap: Tests to Add

```
tests/test_documents_router.py       ← new file; cover all /api/v1/documents/* endpoints
tests/test_users_router.py           ← cover /api/v1/users/me + 401/403 paths
tests/test_auth.py                   ← verify_auth: valid JWT, missing header, sub mismatch
```

### Sample — Upload Success (pytest + TestClient)

```python
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.api.main import app
from app.core.dependencies import get_upload_service

def test_upload_returns_202_with_document_id():
    mock_service = AsyncMock()
    mock_service.process_upload_request.return_value = "doc-uuid-123"
    mock_service.get_s3_key.return_value = "uploads/doc-uuid-123_test.docx"
    mock_service.process_background_upload = AsyncMock()
    app.dependency_overrides[get_upload_service] = lambda: mock_service

    with patch("app.core.dependencies.verify_auth", return_value={"user_id": "user-uuid-1"}):
        client = TestClient(app)
        res = client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": "Bearer t", "x-user-id": "user-uuid-1"},
            data={"templateType": "SDA", "fileName": "test.docx"},
            files={"file": ("test.docx", b"content", "application/octet-stream")},
        )

    assert res.status_code == 202
    body = res.json()
    assert body["documentId"] == "doc-uuid-123"
    assert body["status"] == "PROCESSING"
    app.dependency_overrides.pop(get_upload_service, None)
```

### Sample — Duplicate Filename → 400

```python
def test_upload_duplicate_returns_400():
    mock_service = AsyncMock()
    mock_service.process_upload_request.return_value = None   # None signals duplicate

    app.dependency_overrides[get_upload_service] = lambda: mock_service
    with patch("app.core.dependencies.verify_auth", return_value={"user_id": "user-uuid-1"}):
        client = TestClient(app)
        res = client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": "Bearer t", "x-user-id": "user-uuid-1"},
            data={"templateType": "SDA", "fileName": "dup.docx"},
            files={"file": ("dup.docx", b"content", "application/octet-stream")},
        )

    assert res.status_code == 400
    app.dependency_overrides.pop(get_upload_service, None)
```

---

## Module 2 — Orchestrator (`app/orchestrator/`)

### Critical Flows

| Flow | Test Type |
|------|-----------|
| `POST /orchestrate` → 202 accepted | Unit |
| `_process_document` fans out correct task IDs per template type | Unit |
| `SessionStore.create` — stores session, increments active_count | Unit |
| `SessionStore.record_result` — partial → False; all results → True + event set | Unit |
| `SessionStore.remove` — decrements active_count | Unit |
| `MarkdownSummaryGenerator.generate` — empty results → empty string | Unit |
| `MarkdownSummaryGenerator.generate` — multi-agent results → correct sections | Unit |
| Timeout: session not completed within `ORCHESTRATOR_AGENT_TIMEOUT_SECONDS` → ERROR status | Unit |
| `PARTIAL_COMPLETE` threshold: ≥ threshold results received before timeout | Unit |

### Existing Tests

- `tests/test_orchestrator_session.py` — full SessionStore coverage ✅
- `tests/test_orchestrator_summary.py` — MarkdownSummaryGenerator ✅
- `tests/test_orchestrator_processing.py` — `_process_document` fan-out ✅

### Gap: Tests to Add

```
tests/test_orchestrator_timeout.py    ← timeout → ERROR status written to DB
tests/test_orchestrator_partial.py   ← PARTIAL_COMPLETE threshold logic
```

### Sample — Timeout Writes ERROR Status

```python
import asyncio
from unittest.mock import AsyncMock, patch
import pytest

@pytest.mark.asyncio
async def test_process_document_writes_error_on_timeout():
    with (
        patch("app.orchestrator.main.get_postgres_pool", return_value=AsyncMock()),
        patch("app.orchestrator.main.S3Service") as mock_s3,
        patch("app.orchestrator.main.SQSService"),
        patch("app.orchestrator.main.IngestorService") as mock_ingestor,
        patch("app.orchestrator.main._session_store") as mock_store,
    ):
        mock_s3.return_value.download_file = AsyncMock(return_value=b"content")
        mock_ingestor.return_value.extract_text_from_docx.return_value = "text"

        session_mock = AsyncMock()
        session_mock.completion_event.wait = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_store.create = AsyncMock(return_value=session_mock)

        repo_mock = AsyncMock()
        with patch("app.orchestrator.main.DocumentRepository", return_value=repo_mock):
            from app.orchestrator.main import _process_document
            await _process_document("doc-1", "uploads/doc.docx", "SDA")

        repo_mock.update_status.assert_awaited_with("doc-1", "ERROR")
```

---

## Module 3 — Relay Service (`app/relay_service/`)

### Critical Flows

| Flow | Test Type |
|------|-----------|
| `dispatch` with inline `file_content` → calls correct agent | Unit |
| `dispatch` with `file_content=None` → fetches from S3 | Unit |
| `dispatch` with unknown `agent_type` → StatusMessage with error | Unit |
| Agent raises exception → StatusMessage with error (no propagation) | Unit |
| `run_worker` processes one message and deletes from queue | Unit |
| `run_worker` infrastructure error (DB down) → message remains invisible | Unit |
| `/health` → `{ "status": "ok" }` | Unit |

### Existing Tests

- `tests/test_relay_service.py` — dispatch success, unknown agent, S3 fetch path ✅

### Gap: Tests to Add

```
tests/test_relay_service_polling.py    ← run_worker loop: message processed + deleted
tests/test_relay_service_infra.py      ← DB/S3 error does not delete message
```

### Sample — dispatch with Unknown Agent Type

```python
import pytest
from app.relay_service.worker import dispatch
from app.models.task_message import TaskMessage

@pytest.mark.asyncio
async def test_dispatch_unknown_agent_returns_error_status_message():
    task = TaskMessage(
        task_id="doc1_unknown",
        document_id="doc1",
        agent_type="unknown_type",
        template_type="SDA",
        file_content="text",
        s3_bucket="docsupload",
        s3_key="uploads/doc1.docx",
    )
    from unittest.mock import AsyncMock
    result = await dispatch(task, s3=AsyncMock())

    assert result.task_id == "doc1_unknown"
    assert result.error is not None
    assert "Unknown agent type" in result.error
```

### Sample — dispatch fetches from S3 when file_content is None

```python
@pytest.mark.asyncio
async def test_dispatch_fetches_from_s3_when_content_is_none():
    from unittest.mock import AsyncMock, patch
    from app.relay_service.worker import dispatch
    from app.models.task_message import TaskMessage

    task = TaskMessage(
        task_id="doc2_security",
        document_id="doc2",
        agent_type="security",
        template_type="SDA",
        file_content=None,
        s3_bucket="docsupload",
        s3_key="uploads/doc2.docx",
    )
    s3_mock = AsyncMock()
    s3_mock.download_file.return_value = b"Policy content from S3"

    with patch("app.relay_service.worker.AGENT_REGISTRY", {"security": AsyncMock(return_value=AsyncMock(model_dump=lambda: {}))}):
        await dispatch(task, s3=s3_mock)

    s3_mock.download_file.assert_awaited_once_with(task.s3_key, bucket=task.s3_bucket)
```

---

## Module 4 — DataPipeline (`app/datapipeline/`)

### Critical Flows

| Flow | Test Type |
|------|-----------|
| `QuestionExtractor.extract` — valid LLM JSON response → list of ExtractedQuestion | Unit |
| `QuestionExtractor.extract` — LLM wraps JSON in code fences → stripped correctly | Unit |
| `QuestionExtractor.extract` — malformed JSON → raises / returns empty list | Unit |
| `is_changed` — same hash → False; different hash → True | Unit |
| `page_name_from_url` — returns last path segment without extension | Unit |
| `lambda_function.handler` — happy path executes pipeline | Unit |
| `USE_LOCAL_POLICY_SOURCES=true` → reads from local JSON, not DB | Unit |
| `SAVE_DEBUG_OUTPUT=true` → writes debug files to `DEBUG_OUTPUT_DIR` | Unit |

### Existing Tests

- `app/datapipeline/src/tests/` — check for existing coverage of sync/db utilities.

### Gap: Tests to Add

```
tests/test_datapipeline_extractor.py  ← QuestionExtractor unit tests (mock AnthropicBedrock)
tests/test_datapipeline_sync.py       ← is_changed / upsert_sync_record logic
tests/test_datapipeline_lambda.py     ← lambda_function.handler smoke test
```

### Sample — QuestionExtractor with Mocked LLM

```python
import json
from unittest.mock import MagicMock, patch
from app.datapipeline.src.evaluator import QuestionExtractor

def _make_extractor() -> QuestionExtractor:
    with patch("app.datapipeline.src.evaluator.AnthropicBedrock"):
        return QuestionExtractor(
            aws_access_key="test", aws_secret_key="test",
            aws_region="eu-west-2", model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        )

def test_extract_returns_questions_from_valid_json():
    extractor = _make_extractor()
    questions_json = json.dumps([
        {"question": "Is MFA enforced?", "category": "Security", "source_reference": "C1.a"}
    ])
    extractor._client.messages.create = MagicMock(
        return_value=MagicMock(content=[MagicMock(text=questions_json)])
    )
    results = extractor.extract("https://example.com/policy", "Policy text", "Security")
    assert len(results) == 1
    assert results[0].question == "Is MFA enforced?"

def test_extract_strips_markdown_code_fences():
    extractor = _make_extractor()
    wrapped = "```json\n[{\"question\": \"Q?\", \"category\": \"C\", \"source_reference\": \"R\"}]\n```"
    extractor._client.messages.create = MagicMock(
        return_value=MagicMock(content=[MagicMock(text=wrapped)])
    )
    results = extractor.extract("https://example.com", "content", "Security")
    assert len(results) == 1
```

---

## Running Tests Locally

### Prerequisites

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Ensure LocalStack is running (for SQS/S3 integration tests)
docker compose up localstack -d

# Ensure Postgres is running (for DB integration tests)
docker compose up postgres -d
```

### Run All Tests

```bash
pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=80
```

### Run by Module

```bash
# CoreBackend only
pytest tests/test_upload_router.py tests/test_document_repository.py tests/test_sqs_service.py

# Orchestrator only
pytest tests/test_orchestrator_session.py tests/test_orchestrator_summary.py tests/test_orchestrator_processing.py

# Relay Service only
pytest tests/test_relay_service.py

# DataPipeline only
pytest app/datapipeline/src/tests/
```

### Run with Verbose Output

```bash
pytest -v --tb=short tests/
```

---

## Known Gaps Summary

| Module | Gap | Priority |
|--------|-----|----------|
| CoreBackend | `test_upload_router.py` uses old `/api/upload` path — must update to `/api/v1/documents/upload` | High |
| CoreBackend | No tests for `/api/v1/documents/status`, `/api/v1/documents`, `/api/v1/documents/{id}` | High |
| CoreBackend | No auth unit tests (`verify_auth` scenarios: missing header, sub mismatch) | High |
| Orchestrator | No timeout → ERROR status test | High |
| Orchestrator | No PARTIAL_COMPLETE threshold test | Medium |
| Relay Service | No `run_worker` polling loop test | Medium |
| DataPipeline | No `QuestionExtractor` unit tests | High |
| DataPipeline | No `lambda_function.handler` smoke test | Medium |

---

## Acceptance Criteria

- [ ] All existing tests pass with zero modifications to production code
- [ ] New test files listed in **Gap** section above are implemented
- [ ] `pytest --cov=app --cov-fail-under=80` exits 0
- [ ] No test patches production behaviour — mocks only at I/O boundaries (DB, SQS, S3, LLM)

---

## Governance

- All new tests delivered via pull request
- Green CI required before merge
- No functional refactor performed as part of testing work
