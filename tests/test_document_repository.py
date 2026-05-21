import pytest
from unittest.mock import AsyncMock, MagicMock
from app.orchestrator.src.repositories.document_repository import DocumentRepository

@pytest.mark.asyncio
async def test_update_status_with_result():
    # Setup
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    context = MagicMock()
    context.get_current_timestamp.return_value = "2026-01-01"
    repo = DocumentRepository(pool, context)

    # Execute
    await repo.update_status("doc-1", "Queued", result_md="# Report\nAll good.")

    # Verify
    conn.execute.assert_called_once()
    args = conn.execute.call_args[0]
    assert "result_md" in args[0]
    assert args[1] == "Queued"
    assert args[3] == "# Report\nAll good."
