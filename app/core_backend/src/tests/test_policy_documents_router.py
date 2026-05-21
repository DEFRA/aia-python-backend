from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api.main import app
from utils.dependencies import get_policy_document_service, get_user_repository
from models.user_record import UserRecord
from utils.postgres import get_db_pool

app.dependency_overrides[get_db_pool] = lambda: AsyncMock()

MOCK_USER = UserRecord(userId="user123", email="user@example.com", name="Test User")

BASE_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-User-Id": "user123",
    "Accept": "application/json",
}

client = TestClient(app)


class TestCreatePolicyDocument:
    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_create_policy_document_returns_created_doc(self, _mock_get_user, _mock_auth):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.create_policy_document.return_value = {
            "urlId": 11,
            "filename": "New Policy",
            "category": "security",
            "source": "SharePoint",
            "url": "https://example.com/new-policy",
            "isActive": True,
            "updatedAt": None,
        }
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        payload = {
            "filename": "New Policy",
            "category": "security",
            "source": "SharePoint",
            "url": "https://example.com/new-policy",
            "isActive": True,
        }

        response = client.post("/api/v1/policy-documents", headers=BASE_HEADERS, json=payload)

        assert response.status_code == 201
        assert response.json()["urlId"] == 11

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)

    def test_create_policy_document_no_auth_returns_401(self):
        payload = {
            "filename": "New Policy",
            "category": "security",
            "source": "SharePoint",
            "url": "https://example.com/new-policy",
            "isActive": True,
        }

        response = client.post("/api/v1/policy-documents", json=payload)
        assert response.status_code == 401

    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_create_policy_document_invalid_category_returns_400(
        self, _mock_get_user, _mock_auth
    ):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.create_policy_document.side_effect = ValueError(
            "Unsupported category: invalid"
        )
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        payload = {
            "filename": "New Policy",
            "category": "invalid",
            "source": "SharePoint",
            "url": "https://example.com/new-policy",
            "isActive": True,
        }
        response = client.post(
            "/api/v1/policy-documents", headers=BASE_HEADERS, json=payload
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Unsupported category: invalid"

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)

    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_create_policy_document_duplicate_url_returns_400(
        self, _mock_get_user, _mock_auth
    ):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.create_policy_document.side_effect = ValueError(
            "Policy document with URL already exists: https://example.com/new-policy"
        )
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        payload = {
            "filename": "New Policy",
            "category": "security",
            "source": "SharePoint",
            "url": "https://example.com/new-policy",
            "isActive": True,
        }
        response = client.post(
            "/api/v1/policy-documents", headers=BASE_HEADERS, json=payload
        )

        assert response.status_code == 400
        assert (
            response.json()["detail"]
            == "Policy document with URL already exists: https://example.com/new-policy"
        )

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)


class TestFetchPolicyDocuments:
    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
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
                    "source": "SharePoint",
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


class TestFetchPolicyDocumentOptions:
    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_fetch_policy_document_options_returns_sources_and_categories(
        self, _mock_get_user, _mock_auth
    ):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.fetch_policy_document_options.return_value = {
            "sources": ["SharePoint", "Confluence", "GitHub"],
            "categories": ["security", "technical"],
        }
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        response = client.get("/api/v1/policy-documents/options", headers=BASE_HEADERS)

        assert response.status_code == 200
        body = response.json()
        assert body["sources"] == ["SharePoint", "Confluence", "GitHub"]
        assert body["categories"] == ["security", "technical"]

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)


class TestFetchPolicyDocumentByUrlId:
    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_fetch_policy_document_by_url_id_returns_document(self, _mock_get_user, _mock_auth):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.fetch_policy_document_by_url_id.return_value = {
            "urlId": 10,
            "filename": "Policy",
            "category": "technical",
            "source": "SharePoint",
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

    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
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
    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_update_policy_document_by_url_id_returns_updated_doc(self, _mock_get_user, _mock_auth):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.update_policy_document_by_url_id.return_value = {
            "urlId": 5,
            "filename": "Updated Policy",
            "category": "security",
            "source": "SharePoint",
            "url": "https://example.com/updated-policy",
            "isActive": False,
            "updatedAt": None,
        }
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        payload = {
            "filename": "Updated Policy",
            "category": "security",
            "source": "SharePoint",
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
            "source": "SharePoint",
            "url": "https://example.com/updated-policy",
            "isActive": True,
        }
        response = client.put("/api/v1/policy-documents/5", json=payload)
        assert response.status_code == 401

    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_update_policy_document_invalid_category_returns_400(
        self, _mock_get_user, _mock_auth
    ):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.update_policy_document_by_url_id.side_effect = ValueError(
            "Unsupported category: invalid"
        )
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        payload = {
            "filename": "Updated Policy",
            "category": "invalid",
            "source": "SharePoint",
            "url": "https://example.com/updated-policy",
            "isActive": True,
        }
        response = client.put(
            "/api/v1/policy-documents/5", headers=BASE_HEADERS, json=payload
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Unsupported category: invalid"

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)

    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_update_policy_document_duplicate_url_returns_400(
        self, _mock_get_user, _mock_auth
    ):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.update_policy_document_by_url_id.side_effect = ValueError(
            "Policy document with URL already exists: https://example.com/updated-policy"
        )
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        payload = {
            "filename": "Updated Policy",
            "category": "security",
            "source": "SharePoint",
            "url": "https://example.com/updated-policy",
            "isActive": True,
        }
        response = client.put(
            "/api/v1/policy-documents/5", headers=BASE_HEADERS, json=payload
        )

        assert response.status_code == 400
        assert (
            response.json()["detail"]
            == "Policy document with URL already exists: https://example.com/updated-policy"
        )

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)


class TestDeletePolicyDocumentByUrlId:
    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_delete_policy_document_returns_204(self, _mock_get_user, _mock_auth):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.delete_policy_document_by_url_id.return_value = True
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        response = client.delete("/api/v1/policy-documents/7", headers=BASE_HEADERS)

        assert response.status_code == 204
        assert response.content == b""

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)

    def test_delete_policy_document_no_auth_returns_401(self):
        response = client.delete("/api/v1/policy-documents/7")
        assert response.status_code == 401

    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_delete_policy_document_invalid_url_id_returns_422(
        self, _mock_get_user, _mock_auth
    ):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        response = client.delete("/api/v1/policy-documents/0", headers=BASE_HEADERS)

        assert response.status_code == 422

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)

    @patch("app.core_backend.src.utils.dependencies.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.core_backend.src.utils.dependencies.AuthService.get_user_id", return_value="user123")
    def test_delete_policy_document_returns_404_when_missing(
        self, _mock_get_user, _mock_auth
    ):
        mock_user_repo = AsyncMock()
        mock_user_repo.get_user_by_id.return_value = MOCK_USER
        app.dependency_overrides[get_user_repository] = lambda: mock_user_repo

        mock_service = AsyncMock()
        mock_service.delete_policy_document_by_url_id.return_value = False
        app.dependency_overrides[get_policy_document_service] = lambda: mock_service

        response = client.delete("/api/v1/policy-documents/999", headers=BASE_HEADERS)

        assert response.status_code == 404
        assert response.json()["detail"] == "Policy document '999' not found."

        app.dependency_overrides.pop(get_policy_document_service, None)
        app.dependency_overrides.pop(get_user_repository, None)


