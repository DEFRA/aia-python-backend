from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api.main import app
from utils.dependencies import get_cost_usage_service
from models.cost_usage_record import (
    AgentTokenUsage,
    CostUsageDocument,
    CostUsageResponse,
    CostUsageSummary,
    Pagination,
)
from utils.postgres import get_db_pool

app.dependency_overrides[get_db_pool] = lambda: AsyncMock()

BASE_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-User-Id": "user123",
    "Accept": "application/json",
}

MOCK_DOC_ID = "11111111-1111-1111-1111-111111111111"

client = TestClient(app)


def _sample_response() -> CostUsageResponse:
    return CostUsageResponse(
        costUsage=[
            CostUsageDocument(
                doc_id=MOCK_DOC_ID,
                file_name="a.docx",
                uploadedAt=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
                agents=[
                    AgentTokenUsage(name="Security", inputTokens=100, outputTokens=50)
                ],
                totalCost=0.10,
                currency="USD",
            )
        ],
        pagination=Pagination(
            page=1,
            limit=10,
            total=1,
            totalPages=1,
            hasNext=False,
            hasPrevious=False,
            nextPage=None,
            previousPage=None,
        ),
        summary=CostUsageSummary(
            totalCost=0.10,
            currency="USD",
            totalDocuments=1,
            totalInputTokens=100,
            totalOutputTokens=50,
            totalTokens=150,
        ),
    )


# ---------------------------------------------------------------------------
# GET /api/v1/cost-usage
# ---------------------------------------------------------------------------


class TestFetchCostUsage:
    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_returns_paginated_response(self, mock_get_user, mock_auth):
        mock_service = AsyncMock()
        mock_service.fetch_cost_usage.return_value = _sample_response()
        app.dependency_overrides[get_cost_usage_service] = lambda: mock_service

        response = client.get("/api/v1/cost-usage", headers=BASE_HEADERS)

        assert response.status_code == 200
        body = response.json()
        assert "costUsage" in body
        assert "pagination" in body
        assert "summary" in body
        assert body["pagination"]["total"] == 1
        assert body["summary"]["totalTokens"] == 150
        mock_service.fetch_cost_usage.assert_called_once()

        app.dependency_overrides.pop(get_cost_usage_service, None)

    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_passes_pagination_query_params_to_service(self, mock_get_user, mock_auth):
        mock_service = AsyncMock()
        mock_service.fetch_cost_usage.return_value = _sample_response()
        app.dependency_overrides[get_cost_usage_service] = lambda: mock_service

        response = client.get(
            "/api/v1/cost-usage?page=3&limit=5", headers=BASE_HEADERS
        )

        assert response.status_code == 200
        kwargs = mock_service.fetch_cost_usage.call_args.kwargs
        assert kwargs == {"page": 3, "limit": 5}

        app.dependency_overrides.pop(get_cost_usage_service, None)

    def test_no_auth_returns_401(self):
        response = client.get("/api/v1/cost-usage")
        assert response.status_code == 401

    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_invalid_page_returns_422(self, mock_get_user, mock_auth):
        response = client.get("/api/v1/cost-usage?page=0", headers=BASE_HEADERS)
        assert response.status_code == 422

    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_limit_above_max_returns_422(self, mock_get_user, mock_auth):
        response = client.get("/api/v1/cost-usage?limit=500", headers=BASE_HEADERS)
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/cost-usage/{document_id}
# ---------------------------------------------------------------------------


class TestFetchCostUsageByDoc:
    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_returns_document_when_found(self, mock_get_user, mock_auth):
        mock_service = AsyncMock()
        mock_service.fetch_cost_usage_by_doc.return_value = _sample_response().costUsage[0]
        app.dependency_overrides[get_cost_usage_service] = lambda: mock_service

        response = client.get(
            f"/api/v1/cost-usage/{MOCK_DOC_ID}", headers=BASE_HEADERS
        )

        assert response.status_code == 200
        body = response.json()
        assert body["doc_id"] == MOCK_DOC_ID
        assert body["agents"][0]["name"] == "Security"

        app.dependency_overrides.pop(get_cost_usage_service, None)

    @patch("app.utils.auth.AuthService.authorise_user", return_value={"sub": "user123"})
    @patch("app.utils.auth.AuthService.get_user_id", return_value="user123")
    def test_missing_document_returns_404(self, mock_get_user, mock_auth):
        mock_service = AsyncMock()
        mock_service.fetch_cost_usage_by_doc.return_value = None
        app.dependency_overrides[get_cost_usage_service] = lambda: mock_service

        response = client.get(
            f"/api/v1/cost-usage/{MOCK_DOC_ID}", headers=BASE_HEADERS
        )

        assert response.status_code == 404

        app.dependency_overrides.pop(get_cost_usage_service, None)

    def test_no_auth_returns_401(self):
        response = client.get(f"/api/v1/cost-usage/{MOCK_DOC_ID}")
        assert response.status_code == 401


