from typing import List, Optional

import asyncpg

from app.core.enums import DocumentStatus
from app.models.document_record import DocumentRecord
from app.models.history_record import HistoryRecord
from app.models.result_record import ResultRecord
from app.models.upload_request import UploadRequest
from app.utils.app_context import AppContext
from app.utils.logger import get_logger

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
        self, request: UploadRequest, doc_id: str, user_id: str
    ) -> str:
        now = self.context.get_current_timestamp()
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO document_uploads
                    (doc_id, template_type, user_id, file_name, status,
                     uploaded_ts, processed_ts, status_updated_at, result, result_md, error_message)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, NULL, $6, NULL, NULL, NULL)
                """,
                doc_id,
                request.templateType,
                user_id,
                request.fileName,
                DocumentStatus.PROCESSING.value,
                now,
            )
        logger.info("Inserted document %s for user %s", doc_id, user_id)
        return doc_id

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
                UPDATE document_uploads
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

    async def fetch_history(
        self, user_id: str, page: int = 1, limit: int = 20
    ) -> tuple[List[HistoryRecord], int]:
        offset = (page - 1) * limit
        async with self.pool.acquire() as conn:
            total_row = await conn.fetchrow(
                "SELECT COUNT(*) AS total FROM document_uploads WHERE user_id = $1",
                user_id,
            )
            rows = await conn.fetch(
                """
                SELECT doc_id::text AS "documentId",
                       file_name    AS "originalFilename",
                       template_type AS "templateType",
                       status,
                       uploaded_ts  AS "createdAt",
                       processed_ts AS "completedAt"
                FROM document_uploads
                WHERE user_id = $1
                ORDER BY uploaded_ts DESC
                LIMIT $2 OFFSET $3
                """,
                user_id,
                limit,
                offset,
            )
        records = [
            HistoryRecord(
                documentId=row["documentId"],
                originalFilename=row["originalFilename"],
                templateType=row["templateType"],
                status=row["status"],
                createdAt=row["createdAt"],
                completedAt=row["completedAt"],
            )
            for row in rows
        ]
        total = total_row["total"] if total_row else 0
        return records, total

    async def get_processing_document_ids(self, user_id: str) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT doc_id::text AS "documentId"
                FROM document_uploads
                WHERE user_id = $1 AND status = $2
                ORDER BY uploaded_ts DESC
                """,
                user_id,
                DocumentStatus.PROCESSING.value,
            )
        return [row["documentId"] for row in rows]

    async def fetch_result(self, doc_id: str, user_id: str) -> Optional[ResultRecord]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT doc_id::text  AS "documentId",
                       file_name     AS "originalFilename",
                       template_type AS "templateType",
                       status,
                       result_md     AS "resultMd",
                       error_message AS "errorMessage",
                       uploaded_ts   AS "createdAt",
                       processed_ts  AS "completedAt"
                FROM document_uploads
                WHERE doc_id = $1::uuid AND user_id = $2
                """,
                doc_id,
                user_id,
            )
        if row is None:
            return None
        return ResultRecord(
            documentId=row["documentId"],
            originalFilename=row["originalFilename"],
            templateType=row["templateType"],
            status=row["status"],
            resultMd=row["resultMd"],
            errorMessage=row["errorMessage"],
            createdAt=row["createdAt"],
            completedAt=row["completedAt"],
        )

    async def claim_pending_documents(self, limit: int = 10) -> List[DocumentRecord]:
        """
        Atomically claims PROCESSING documents for the ingestor worker.
        Uses a separate 'Claimed' internal marker to avoid double-processing.
        """
        now = self.context.get_current_timestamp()
        async with self.pool.acquire() as conn:
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
                SET status = 'CLAIMED', status_updated_at = $3
                FROM claimed
                WHERE document_uploads.doc_id = claimed.doc_id
                RETURNING document_uploads.doc_id::text, user_id, template_type,
                          file_name, status, uploaded_ts
                """,
                DocumentStatus.PROCESSING.value,
                limit,
                now,
            )
        return [
            DocumentRecord(
                doc_id=row["doc_id"],
                user_id=row["user_id"],
                template_type=row["template_type"],
                file_name=row["file_name"],
                status=row["status"],
                uploaded_ts=row["uploaded_ts"],
            )
            for row in rows
        ]

    async def cleanup_stuck_documents(self, timeout_minutes: int = 15) -> int:
        from datetime import timedelta

        now = self.context.get_current_timestamp()
        threshold = now - timedelta(minutes=timeout_minutes)
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE document_uploads
                SET status = $1, status_updated_at = $2
                WHERE status = 'CLAIMED' AND status_updated_at < $3
                """,
                DocumentStatus.PROCESSING.value,
                now,
                threshold,
            )
        try:
            count = int(result.split()[1])
        except (IndexError, ValueError):
            count = 0
        if count > 0:
            logger.warning("Reset %d stuck CLAIMED documents back to PROCESSING", count)
        return count
