from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.cost_usage_repository import CostUsageRepository


def _make_pool(fetch_return):
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch.return_value = fetch_return
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool, conn


@pytest.mark.asyncio
async def test_fetch_all_cost_usage_returns_rows_for_user():
    rows = [
        {
            "doc_id": "doc-1",
            "file_name": "f.docx",
            "uploaded_ts": "2026-05-01T10:00:00Z",
            "agent_name": "Security",
            "input_tokens": 100,
            "output_tokens": 50,
            "unit_cost": 0.0012,
        }
    ]
    pool, conn = _make_pool(rows)
    repo = CostUsageRepository(pool)

    result = await repo.fetch_all_cost_usage("user-1")

    assert result == rows
    conn.fetch.assert_awaited_once()
    sql, *params = conn.fetch.call_args[0]
    assert "FROM backend.document_uploads du" in sql
    assert "JOIN backend.cost_usage cu" in sql
    assert "WHERE du.user_id = $1" in sql
    assert "ORDER BY du.uploaded_ts DESC" in sql
    assert params == ["user-1"]


@pytest.mark.asyncio
async def test_fetch_all_cost_usage_returns_empty_when_no_rows():
    pool, conn = _make_pool([])
    repo = CostUsageRepository(pool)

    result = await repo.fetch_all_cost_usage("user-1")

    assert result == []
    conn.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_cost_usage_by_doc_filters_by_doc_and_user():
    rows = [
        {
            "doc_id": "doc-1",
            "file_name": "f.docx",
            "uploaded_ts": "2026-05-01T10:00:00Z",
            "agent_name": "Security",
            "input_tokens": 100,
            "output_tokens": 50,
            "unit_cost": 0.0012,
        }
    ]
    pool, conn = _make_pool(rows)
    repo = CostUsageRepository(pool)

    result = await repo.fetch_cost_usage_by_doc("doc-1", "user-1")

    assert result == rows
    sql, *params = conn.fetch.call_args[0]
    assert "WHERE du.user_id = $1 AND du.doc_id = $2::uuid" in sql
    assert "ORDER BY cu.agent_name ASC" in sql
    assert params == ["user-1", "doc-1"]


@pytest.mark.asyncio
async def test_fetch_cost_usage_by_doc_returns_empty_when_doc_missing():
    pool, conn = _make_pool([])
    repo = CostUsageRepository(pool)

    result = await repo.fetch_cost_usage_by_doc("doc-x", "user-1")

    assert result == []
    conn.fetch.assert_awaited_once()
