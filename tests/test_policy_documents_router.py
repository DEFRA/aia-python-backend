from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.dependencies import get_policy_document_service, get_user_repository
from app.models.user_record import UserRecord
from app.utils.postgres import get_db_pool

app.dependency_overrides[get_db_pool] = lambda: AsyncMock()

MOCK_USER = UserRecord(userId="user123", email="user@example.com", name="Test User")

BASE_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-User-Id": "user123",
    "Accept": "application/json",
}

client = TestClient(app)


class TestFetchPolicyDocuments:
    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_fetch_policy_documents_returns_paginated_list(self, _mock_get_user, _mock_auth):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.fetch_policy_documents.return_value = {
            "documents": [
                {
                    "urlId": 1,
                    "filename": "Secure by Design",
                    "category": "security",
                    "source": "page",
                    "url": "https://example.com/policy",
                    "isActive": True,
                    "updatedAt": None,
                }
            ],
            "total": 1,
            "page": 1,
            "limit": 20,
        }
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        response = client.get("/api/v1/policy-documents?page=1&limit=20", headers=BASE_HEADERS)

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert len(body["documents"]) == 1
        assert body["documents"][0]["urlId"] == 1

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)


class TestFetchPolicyDocumentByUrlId:
    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_fetch_policy_document_by_url_id_returns_document(self, _mock_get_user, _mock_auth):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.fetch_policy_document_by_url_id.return_value = {
            "urlId": 10,
            "filename": "Policy",
            "category": "technical",
            "source": "page",
            "url": "https://example.com/policy-10",
            "isActive": True,
            "updatedAt": None,
        }
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        response = client.get("/api/v1/policy-documents/10", headers=BASE_HEADERS)

        assert response.status_code == 200
        assert response.json()["urlId"] == 10

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)

    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_fetch_policy_document_by_url_id_returns_404_when_missing(self, _mock_get_user, _mock_auth):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.fetch_policy_document_by_url_id.return_value = None
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        response = client.get("/api/v1/policy-documents/999", headers=BASE_HEADERS)

        assert response.status_code == 404

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)


class TestUpdatePolicyDocumentByUrlId:
    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_update_policy_document_by_url_id_returns_updated_doc(self, _mock_get_user, _mock_auth):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.update_policy_document_by_url_id.return_value = {
            "urlId": 5,
            "filename": "Updated Policy",
            "category": "security",
            "source": "page",
            "url": "https://example.com/updated-policy",
            "isActive": False,
            "updatedAt": None,
        }
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        payload = {
            "filename": "Updated Policy",
            "category": "security",
            "source": "page",
            "url": "https://example.com/updated-policy",
            "isActive": False,
        }
        response = client.put("/api/v1/policy-documents/5", headers=BASE_HEADERS, json=payload)

        assert response.status_code == 200
        assert response.json()["urlId"] == 5
        assert response.json()["isActive"] is False

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)

    def test_update_policy_document_no_auth_returns_401(self):
        payload = {
            "filename": "Updated Policy",
            "category": "security",
            "source": "page",
            "url": "https://example.com/updated-policy",
            "isActive": True,
        }
        response = client.put("/api/v1/policy-documents/5", json=payload)
        assert response.status_code == 401
