from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from utils.postgres import get_db_pool
from utils.dependencies import get_upload_service, get_user_repository
from api.main import app
from models.user_record import UserRecord

app.dependency_overrides[get_db_pool] = lambda: AsyncMock()

MOCK_USER = UserRecord(userId="user123", email="user@example.com", name="Test User")

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
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

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
        app.dependency_overrides.pop(get_user_repository, None)


# ---------------------------------------------------------------------------
# POST /api/v1/documents/upload — duplicate filename
# ---------------------------------------------------------------------------


class TestUploadDuplicate:
    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_duplicate_returns_400(self, mock_get_user, mock_auth):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

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
        app.dependency_overrides.pop(get_user_repository, None)


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

    @patch("app.utils.auth.AuthService.authorise_user", return_value="some-token")
    @patch("app.utils.auth.AuthService.get_user_id", return_value="different-user")
    def test_sub_mismatch_returns_401(self, mock_get_user, mock_auth):
        """JWT sub does not match X-USER-ID header → 401."""
        response = client.post(
            "/api/v1/documents/upload",
            headers=BASE_HEADERS,  # X-User-Id: user123, but JWT sub returns different-user
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"content", "application/pdf")},
        )
        assert response.status_code == 401

    @patch("app.utils.auth.AuthService.authorise_user", return_value="some-token")
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_unknown_user_returns_401(self, mock_get_user, mock_auth):
        """JWT valid and sub matches X-USER-ID, but user not found in DB → 401."""
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = None
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        response = client.post(
            "/api/v1/documents/upload",
            headers=BASE_HEADERS,
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"content", "application/pdf")},
        )
        assert response.status_code == 401

        app.dependency_overrides.pop(get_user_repository, None)

    @patch(
        "app.utils.auth.AuthService.authorise_user",
        side_effect=__import__("fastapi").HTTPException(
            status_code=401, detail="Token has expired"
        ),
    )
    def test_expired_jwt_returns_401(self, mock_auth):
        """Expired JWT → 401."""
        response = client.post(
            "/api/v1/documents/upload",
            headers=BASE_HEADERS,
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"content", "application/pdf")},
        )
        assert response.status_code == 401

    @patch("app.utils.auth.AuthService.authorise_user", return_value="some-token")
    @patch(
        "app.utils.auth.AuthService.get_user_id",
        side_effect=__import__("fastapi").HTTPException(
            status_code=401, detail="Invalid token: missing issuance time (iat) claim"
        ),
    )
    def test_missing_iat_claim_returns_401(self, mock_get_user, mock_auth):
        """JWT missing 'iat' claim → 401."""
        response = client.post(
            "/api/v1/documents/upload",
            headers=BASE_HEADERS,
            data={
                "templateType": "CHEDP",
                "fileName": "test.pdf",
            },
            files={"file": ("test.pdf", b"content", "application/pdf")},
        )
        assert response.status_code == 401

    @patch("app.utils.auth.AuthService.authorise_user", return_value="some-token")
    @patch(
        "app.utils.auth.AuthService.get_user_id",
        side_effect=__import__("fastapi").HTTPException(
            status_code=401, detail="Invalid token: issuance time is in the future"
        ),
    )
    def test_iat_in_future_returns_401(self, mock_get_user, mock_auth):
        """JWT 'iat' claim is in future (clock skew/tampering) → 401."""
        response = client.post(
            "/api/v1/documents/upload",
            headers=BASE_HEADERS,
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
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

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
        app.dependency_overrides.pop(get_user_repository, None)

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
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.fetch_result.return_value = None
        app.dependency_overrides[get_upload_service] = lambda: mock_service

        response = client.get(
            f"/api/v1/documents/{MOCK_DOC_ID}",
            headers=BASE_HEADERS,
        )
        assert response.status_code == 404

        app.dependency_overrides.pop(get_upload_service, None)
        app.dependency_overrides.pop(get_user_repository, None)

    def test_result_no_auth_returns_401(self):
        response = client.get(f"/api/v1/documents/{MOCK_DOC_ID}")
        assert response.status_code == 401


