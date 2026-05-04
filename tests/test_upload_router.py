from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.utils.postgres import get_db_pool
from app.core.dependencies import get_upload_service
from app.api.main import app

app.dependency_overrides[get_db_pool] = lambda: AsyncMock()

BASE_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-User-Id": "user123",
    "Accept": "application/json",
}

MOCK_DOC_ID = "11111111-1111-1111-1111-111111111111"

client = TestClient(app)

# ---------------------------------------------------------------------------
# POST /api/v1/documents/upload — success
# ---------------------------------------------------------------------------


class TestUploadSuccess:
    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_upload_returns_doc_id(self, mock_get_user, mock_auth):
        mock_service = AsyncMock()
        mock_service.process_upload_request.return_value = MOCK_DOC_ID
        mock_service.get_s3_key.return_value = f"{MOCK_DOC_ID}_test.pdf"
        mock_service.process_background_upload = AsyncMock()
        app.dependency_overrides[get_upload_service] = lambda: mock_service

        response = client.post(
            "/api/v1/documents/upload",
            headers=BASE_HEADERS,
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"fake-pdf-content", "application/pdf")},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["documentId"] == MOCK_DOC_ID

        app.dependency_overrides.pop(get_upload_service, None)


# ---------------------------------------------------------------------------
# POST /api/v1/documents/upload — duplicate filename
# ---------------------------------------------------------------------------


class TestUploadDuplicate:
    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_duplicate_returns_400(self, mock_get_user, mock_auth):
        mock_service = AsyncMock()
        mock_service.process_upload_request.return_value = None
        app.dependency_overrides[get_upload_service] = lambda: mock_service

        response = client.post(
            "/api/v1/documents/upload",
            headers=BASE_HEADERS,
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"fake-pdf-content", "application/pdf")},
        )
        assert response.status_code == 400

        app.dependency_overrides.pop(get_upload_service, None)


# ---------------------------------------------------------------------------
# POST /api/v1/documents/upload — missing auth
# ---------------------------------------------------------------------------


class TestUploadAuth:
    def test_missing_bearer_returns_401(self):
        response = client.post(
            "/api/v1/documents/upload",
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
            "/api/v1/documents/upload",
            headers={"Authorization": "Bearer test-token", "Accept": "application/json"},
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"content", "application/pdf")},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/documents — upload history
# ---------------------------------------------------------------------------


class TestFetchHistory:
    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_fetch_history_returns_list(self, mock_get_user, mock_auth):
        mock_service = AsyncMock()
        mock_service.fetch_history.return_value = ([], 0)
        app.dependency_overrides[get_upload_service] = lambda: mock_service

        response = client.get(
            "/api/v1/documents",
            headers=BASE_HEADERS,
        )
        assert response.status_code == 200
        body = response.json()
        assert "documents" in body
        assert isinstance(body["documents"], list)
        mock_service.fetch_history.assert_called_once()

        app.dependency_overrides.pop(get_upload_service, None)

    def test_fetch_history_no_auth_returns_401(self):
        response = client.get("/api/v1/documents")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/documents/{document_id}
# ---------------------------------------------------------------------------


class TestGetResult:
    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_result_not_found_returns_404(self, mock_get_user, mock_auth):
        mock_service = AsyncMock()
        mock_service.fetch_result.return_value = None
        app.dependency_overrides[get_upload_service] = lambda: mock_service

        response = client.get(
            f"/api/v1/documents/{MOCK_DOC_ID}",
            headers=BASE_HEADERS,
        )
        assert response.status_code == 404

        app.dependency_overrides.pop(get_upload_service, None)

    def test_result_no_auth_returns_401(self):
        response = client.get(f"/api/v1/documents/{MOCK_DOC_ID}")
        assert response.status_code == 401
