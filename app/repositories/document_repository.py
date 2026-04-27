import json
from typing import List, Optional
from datetime import timedelta
import asyncpg

from app.models.document_record import DocumentRecord
from app.models.upload_request import UploadRequest
from app.models.history_record import HistoryRecord
from app.models.result_record import ResultRecord
from app.core.enums import UploadStatus
from app.utils.logger import get_logger
from app.utils.app_context import AppContext

logger = get_logger(__name__)

class DocumentRepository:
    def __init__(self, pool: asyncpg.Pool, context: AppContext):
        self.pool = pool
        self.context = context

    async def check_duplicate(self, user_id: str, file_name: str) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT doc_id FROM document_uploads WHERE user_id = $1 AND file_name = $2",
                user_id,
                file_name,
            )
        return row is not None

    async def insert_document(
        self,
        request: UploadRequest,
        doc_id: str,
        user_id: str,
    ) -> str:
        uploaded_ts = self.context.get_current_timestamp()
        processed_ts = None
        status = UploadStatus.UPLOADING.value
        result_json = None

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO document_uploads
                    (doc_id, template_type, user_id, file_name, status,
                     uploaded_ts, processed_ts, status_updated_at, result)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                """,
                doc_id,
                request.templateType,
                user_id,
                request.fileName,
                status,
                uploaded_ts,
                processed_ts,
                uploaded_ts,
                result_json,
            )

        logger.info("Inserted document %s for user %s", doc_id, user_id)
        return doc_id

    async def claim_pending_documents(self, limit: int = 10) -> List[DocumentRecord]:
        """
        Atomically claims documents in 'Analysing' status by moving them to 'Processing'.
        Uses FOR UPDATE SKIP LOCKED for high-concurrency safety.
        """
        current_ts = self.context.get_current_timestamp()
        async with self.pool.acquire() as conn:
            # We use a CTE to claim and return the records in one atomic step
            rows = await conn.fetch(
                """
                WITH claimed AS (
                    SELECT doc_id
                    FROM document_uploads
                    WHERE status = $1
                    ORDER BY uploaded_ts ASC
                    LIMIT $2
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE document_uploads
                SET status = $3, status_updated_at = $4
                FROM claimed
                WHERE document_uploads.doc_id = claimed.doc_id
                RETURNING document_uploads.doc_id::text, user_id, template_type, file_name, status, uploaded_ts
                """,
                UploadStatus.ANALYSING.value,
                limit,
                UploadStatus.CLAIMED.value,
                current_ts,
            )

        records = []
        for row in rows:
            records.append(
                DocumentRecord(
                    doc_id=row["doc_id"],
                    user_id=row["user_id"],
                    template_type=row["template_type"],
                    file_name=row["file_name"],
                    status=row["status"],
                    uploaded_ts=row["uploaded_ts"]
                )
            )
        return records

    async def update_status(self, doc_id: str, status: str, result: Optional[dict] = None) -> None:
        """Updates document status and optionally the result JSON."""
        processed_ts = self.context.get_current_timestamp()
        async with self.pool.acquire() as conn:
            if result is not None:
                await conn.execute(
                    """
                    UPDATE document_uploads
                    SET status = $1, processed_ts = $2, status_updated_at = $2, result = $3::jsonb
                    WHERE doc_id = $4::uuid
                    """,
                    status,
                    processed_ts,
                    json.dumps(result),
                    doc_id,
                )
            else:
                await conn.execute(
                    """
                    UPDATE document_uploads
                    SET status = $1, processed_ts = $2, status_updated_at = $2
                    WHERE doc_id = $3::uuid
                    """,
                    status,
                    processed_ts,
                    doc_id,
                )

    async def cleanup_stuck_documents(self, timeout_minutes: int = 15) -> int:
        """
        Resets 'Claimed' documents back to 'Analysing' if they have been stuck
        for longer than `timeout_minutes`. This handles worker crashes during extraction.
        Returns the number of documents reset.
        """
        current_ts = self.context.get_current_timestamp()
        threshold_ts = current_ts - timedelta(minutes=timeout_minutes)
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE document_uploads
                SET status = $1, status_updated_at = $2
                WHERE status = $3 
                  AND status_updated_at < $4
                """,
                UploadStatus.ANALYSING.value,
                current_ts,
                UploadStatus.CLAIMED.value,
                threshold_ts
            )
            # asyncpg execute returns a string like "UPDATE 3"
            try:
                count = int(result.split()[1])
            except (IndexError, ValueError):
                count = 0
            
            if count > 0:
                logger.warning("Reset %d stuck 'Claimed' documents back to 'Analysing'", count)
            return count

    async def fetch_history(self, user_id: str) -> List[HistoryRecord]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT doc_id::text, template_type, file_name, status,
                       uploaded_ts
                FROM document_uploads
                WHERE user_id = $1
                ORDER BY uploaded_ts DESC
                """,
                user_id,
            )

        records = []
        for row in rows:
            records.append(
                HistoryRecord(
                    doc_id=row["doc_id"],
                    template_type=row["template_type"],
                    file_name=row["file_name"],
                    status=row["status"],
                    uploaded_ts=row["uploaded_ts"]
                )
            )
        return records

    async def fetch_result(self, doc_id: str) -> Optional[ResultRecord]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT file_name, result
                FROM document_uploads
                WHERE doc_id = $1::uuid
                """,
                doc_id,
            )

        if row is None:
            return None

        result_value = json.loads(row["result"]) if row["result"] else None
        return ResultRecord(
            file_name=row["file_name"],
            result=result_value,
        )
