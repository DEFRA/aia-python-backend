from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services.cost_usage_service import CostUsageService


def _row(
    doc_id: str,
    file_name: str,
    uploaded_ts: datetime,
    agent_name: str,
    input_tokens: int,
    output_tokens: int,
    total_cost_usd: float,
) -> dict:
    return {
        "doc_id": doc_id,
        "file_name": file_name,
        "uploaded_ts": uploaded_ts,
        "agent_name": agent_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_cost_usd": total_cost_usd,
    }


def _build_repo(rows):
    repo = AsyncMock()
    repo.fetch_all_cost_usage.return_value = rows
    return repo


# ---------------------------------------------------------------------------
# fetch_cost_usage — grouping + summary + pagination
# ---------------------------------------------------------------------------


class TestFetchCostUsage:
    @pytest.mark.asyncio
    async def test_groups_rows_by_document_and_sums_total_cost(self):
        ts = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
        rows = [
            _row("doc-1", "a.docx", ts, "Security",     100, 50, 0.10),
            _row("doc-1", "a.docx", ts, "Technology",   200, 80, 0.20),
            _row("doc-1", "a.docx", ts, "Architecture", 300, 90, 0.30),
        ]
        service = CostUsageService(_build_repo(rows))

        response = await service.fetch_cost_usage("user-1")

        assert len(response.costUsage) == 1
        doc = response.costUsage[0]
        assert doc.doc_id == "doc-1"
        assert doc.file_name == "a.docx"
        assert [a.name for a in doc.agents] == ["Security", "Technology", "Architecture"]
        assert doc.totalCost == pytest.approx(0.60)
        assert doc.currency == "USD"

    @pytest.mark.asyncio
    async def test_summary_aggregates_across_full_dataset(self):
        ts1 = datetime(2026, 5, 1, tzinfo=timezone.utc)
        ts2 = datetime(2026, 5, 2, tzinfo=timezone.utc)
        rows = [
            _row("doc-1", "a.docx", ts1, "Security",   100, 50, 0.10),
            _row("doc-1", "a.docx", ts1, "Technology", 200, 80, 0.20),
            _row("doc-2", "b.docx", ts2, "Security",   400, 60, 0.40),
        ]
        service = CostUsageService(_build_repo(rows))

        response = await service.fetch_cost_usage("user-1")

        assert response.summary.totalDocuments == 2
        assert response.summary.totalInputTokens == 700
        assert response.summary.totalOutputTokens == 190
        assert response.summary.totalTokens == 890
        assert response.summary.totalCost == pytest.approx(0.70)

    @pytest.mark.asyncio
    async def test_pagination_is_applied_at_document_level(self):
        rows = []
        for i in range(5):
            ts = datetime(2026, 5, 1 + i, tzinfo=timezone.utc)
            rows.extend(
                [
                    _row(f"doc-{i}", f"f-{i}.docx", ts, "Security",   100, 50, 0.1),
                    _row(f"doc-{i}", f"f-{i}.docx", ts, "Technology", 200, 80, 0.2),
                ]
            )
        service = CostUsageService(_build_repo(rows))

        response = await service.fetch_cost_usage("user-1", page=2, limit=2)

        # Two docs per page; doc list is ordered as the repo returns it.
        assert len(response.costUsage) == 2
        assert response.pagination.page == 2
        assert response.pagination.limit == 2
        assert response.pagination.total == 5  # five docs, not ten rows
        assert response.pagination.totalPages == 3
        assert response.pagination.hasNext is True
        assert response.pagination.hasPrevious is True
        assert response.pagination.nextPage == 3
        assert response.pagination.previousPage == 1

    @pytest.mark.asyncio
    async def test_pagination_first_page_has_no_previous(self):
        ts = datetime(2026, 5, 1, tzinfo=timezone.utc)
        rows = [_row("doc-1", "a.docx", ts, "Security", 100, 50, 0.1)]
        service = CostUsageService(_build_repo(rows))

        response = await service.fetch_cost_usage("user-1", page=1, limit=10)

        assert response.pagination.total == 1
        assert response.pagination.totalPages == 1
        assert response.pagination.hasNext is False
        assert response.pagination.hasPrevious is False
        assert response.pagination.nextPage is None
        assert response.pagination.previousPage is None

    @pytest.mark.asyncio
    async def test_empty_dataset_returns_zeroed_summary_and_pagination(self):
        service = CostUsageService(_build_repo([]))

        response = await service.fetch_cost_usage("user-1")

        assert response.costUsage == []
        assert response.summary.totalDocuments == 0
        assert response.summary.totalInputTokens == 0
        assert response.summary.totalOutputTokens == 0
        assert response.summary.totalTokens == 0
        assert response.summary.totalCost == 0.0
        assert response.pagination.total == 0
        assert response.pagination.totalPages == 0
        assert response.pagination.hasNext is False
        assert response.pagination.hasPrevious is False

    @pytest.mark.asyncio
    async def test_negative_or_zero_page_is_clamped(self):
        ts = datetime(2026, 5, 1, tzinfo=timezone.utc)
        rows = [_row("doc-1", "a.docx", ts, "Security", 100, 50, 0.1)]
        service = CostUsageService(_build_repo(rows))

        response = await service.fetch_cost_usage("user-1", page=0, limit=0)

        assert response.pagination.page == 1
        assert response.pagination.limit == 1


# ---------------------------------------------------------------------------
# fetch_cost_usage_by_doc
# ---------------------------------------------------------------------------


class TestFetchCostUsageByDoc:
    @pytest.mark.asyncio
    async def test_returns_none_when_doc_has_no_rows(self):
        repo = AsyncMock()
        repo.fetch_cost_usage_by_doc.return_value = []
        service = CostUsageService(repo)

        result = await service.fetch_cost_usage_by_doc("doc-x", "user-1")

        assert result is None

    @pytest.mark.asyncio
    async def test_builds_single_document_with_summed_cost(self):
        ts = datetime(2026, 5, 1, tzinfo=timezone.utc)
        rows = [
            _row("doc-1", "a.docx", ts, "Security",   100, 50, 0.10),
            _row("doc-1", "a.docx", ts, "Governance", 200, 80, 0.25),
        ]
        repo = AsyncMock()
        repo.fetch_cost_usage_by_doc.return_value = rows
        service = CostUsageService(repo)

        result = await service.fetch_cost_usage_by_doc("doc-1", "user-1")

        assert result is not None
        assert result.doc_id == "doc-1"
        assert [a.name for a in result.agents] == ["Security", "Governance"]
        assert result.totalCost == pytest.approx(0.35)
        assert result.currency == "USD"
