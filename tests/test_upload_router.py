import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.utils.postgres import get_db_pool
from app.main import app

app.dependency_overrides[get_db_pool] = lambda: _make_pool()

BASE_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-User-Id": "user123",
    "Accept": "application/json",
}

MOCK_DOC_ID = "11111111-1111-1111-1111-111111111111"

MOCK_RECORD = {
    "doc_id": MOCK_DOC_ID,
    "template_type": "CHEDP",
    "user_id": "user123",
    "file_name": "test.pdf",
    "status": "pending",
    "uploaded_ts": datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc),
    "processed_ts": None,
    "result": None,
}

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool(is_duplicate: bool = False, doc_id: str = MOCK_DOC_ID, records=None):
    """Return a mock asyncpg pool that satisfies the service layer calls."""
    pool = MagicMock()

    # check_duplicate
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    return pool


# ---------------------------------------------------------------------------
# POST /api/upload — success
# ---------------------------------------------------------------------------

class TestUploadSuccess:
    @patch("app.api.upload.service.check_duplicate", new_callable=AsyncMock, return_value=False)
    @patch("app.api.upload.upload_file_to_s3", new_callable=AsyncMock)
    @patch("app.api.upload.service.insert_document", new_callable=AsyncMock, return_value=MOCK_DOC_ID)
    @patch("app.api.upload.uuid.uuid4", return_value=uuid.UUID(MOCK_DOC_ID))
    def test_upload_returns_doc_id(self, mock_uuid, mock_insert, mock_s3, mock_dup):
        response = client.post(
            "/api/upload",
            headers=BASE_HEADERS,
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"fake-pdf-content", "application/pdf")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["docId"] == MOCK_DOC_ID
        assert body["statusCode"] == 200
        assert body["errorMessage"] == ""


# ---------------------------------------------------------------------------
# POST /api/upload — duplicate filename
# ---------------------------------------------------------------------------

class TestUploadDuplicate:
    @patch("app.api.upload.service.check_duplicate", new_callable=AsyncMock, return_value=True)
    def test_duplicate_returns_400(self, mock_dup):
        response = client.post(
            "/api/upload",
            headers=BASE_HEADERS,
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"fake-pdf-content", "application/pdf")},
        )
        assert response.status_code == 200  # HTTP envelope is 200
        body = response.json()
        assert body["statusCode"] == 400
        assert body["docId"] == ""
        assert "test.pdf" in body["errorMessage"]


# ---------------------------------------------------------------------------
# POST /api/upload — missing auth
# ---------------------------------------------------------------------------

class TestUploadAuth:
    def test_missing_bearer_returns_401(self):
        response = client.post(
            "/api/upload",
            headers={"Accept": "application/json"},
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"content", "application/pdf")},
        )
        assert response.status_code == 401

    def test_missing_user_id_returns_401(self):
        response = client.post(
            "/api/upload",
            headers={"Authorization": "Bearer test-token", "Accept": "application/json"},
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"content", "application/pdf")},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/fetchUploadHistory
# ---------------------------------------------------------------------------

class TestFetchHistory:
    @patch(
        "app.api.upload.service.fetch_history",
        new_callable=AsyncMock,
        return_value=[],
    )
    def test_fetch_history_returns_list(self, mock_fetch):
        response = client.get(
            "/api/fetchUploadHistory",
            headers=BASE_HEADERS,
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)
        mock_fetch.assert_called_once()

    def test_fetch_history_no_auth_returns_401(self):
        response = client.get("/api/fetchUploadHistory")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/result
# ---------------------------------------------------------------------------

class TestGetResult:
    @patch(
        "app.api.upload.service.fetch_result",
        new_callable=AsyncMock,
        return_value=None,
    )
    def test_result_not_found_returns_404(self, mock_fetch):
        response = client.get(
            f"/api/result?docID={MOCK_DOC_ID}",
            headers=BASE_HEADERS,
        )
        assert response.status_code == 404

    def test_result_no_auth_returns_401(self):
        response = client.get(f"/api/result?docID={MOCK_DOC_ID}")
        assert response.status_code == 401
