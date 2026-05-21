from unittest.mock import AsyncMock, MagicMock

import pytest

from app.orchestrator.src.repositories.cost_usage_repository import CostUsageRepository


def _make_pool():
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool, conn


@pytest.mark.asyncio
async def test_upsert_cost_usage_executes_update_then_insert_cte():
    pool, conn = _make_pool()
    repo = CostUsageRepository(pool)

    await repo.upsert_cost_usage(
        doc_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_name="security",
        input_tokens=1200,
        output_tokens=480,
        total_cost_usd=0.0,
    )

    conn.execute.assert_awaited_once()
    sql, *params = conn.execute.call_args[0]
    assert "pg_advisory_xact_lock" in sql
    assert "updated AS" in sql
    assert "UPDATE backend.cost_usage" in sql
    assert "INSERT INTO backend.cost_usage" in sql
    assert params == [
        "aaaaaaaa-0000-0000-0000-000000000001",
        "security",
        1200,
        480,
        0.0,
    ]
