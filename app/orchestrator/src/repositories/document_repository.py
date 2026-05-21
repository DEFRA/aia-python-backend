from typing import Optional

import asyncpg

from ..utils.app_context import AppContext
from ..utils.logger import get_logger

logger = get_logger(__name__)


class DocumentRepository:
    """Shared, orchestrator-side surface.

    The full repository (insert/check_duplicate/fetch_history/fetch_result/
    get_processing_document_ids/claim_pending_documents/cleanup_stuck_documents)
    lives at app/core/src/app/repositories/document_repository.py and is only
    available when app/core/src is on PYTHONPATH (core-backend service + its tests).
    """

    def __init__(self, pool: asyncpg.Pool, context: AppContext):
        self.pool = pool
        self.context = context

    async def update_status(
        self,
        doc_id: str,
        status: str,
        result_md: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        now = self.context.get_current_timestamp()
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE backend.document_uploads
                SET status = $1,
                    processed_ts = $2,
                    status_updated_at = $2,
                    result_md = COALESCE($3, result_md),
                    error_message = COALESCE($4, error_message)
                WHERE doc_id = $5::uuid
                """,
                status,
                now,
                result_md,
                error_message,
                doc_id,
            )
