import pytest
from unittest.mock import AsyncMock, MagicMock
from app.repositories.document_repository import DocumentRepository
from app.core.enums import UploadStatus

@pytest.mark.asyncio
async def test_claim_pending_documents():
    # Setup
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    
    # Mock database rows
    mock_row = {
        "doc_id": "doc-1",
        "user_id": "user-1",
        "template_type": "type-1",
        "file_name": "file.docx",
        "status": "Processing",
        "uploaded_ts": "2026-01-01"
    }
    conn.fetch.return_value = [mock_row]
    
    context = MagicMock()
    repo = DocumentRepository(pool, context)
    
    # Execute
    records = await repo.claim_pending_documents(limit=5)
    
    # Verify
    assert len(records) == 1
    assert records[0].doc_id == "doc-1"
    assert records[0].status == "Processing"
    
    # Verify SQL
    conn.fetch.assert_called_once()
    sql = conn.fetch.call_args[0][0]
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "UPDATE document_uploads" in sql

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
    await repo.update_status("doc-1", "Ingested", result={"text": "hi"})
    
    # Verify
    conn.execute.assert_called_once()
    args = conn.execute.call_args[0]
    assert "result = $3::jsonb" in args[0]
    assert args[1] == "Ingested"
    assert "hi" in args[3] # JSON string
