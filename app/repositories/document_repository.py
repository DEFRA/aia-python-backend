import json
from typing import List, Optional
import asyncpg

from app.models.document_record import DocumentRecord
from app.models.upload_request import UploadRequest
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
        status = UploadStatus.ANALYSING.value
        result_json = None

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO document_uploads
                    (doc_id, template_type, user_id, file_name, status,
                     uploaded_ts, processed_ts, result)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::jsonb)
                """,
                doc_id,
                request.templateType,
                user_id,
                request.fileName,
                status,
                uploaded_ts,
                processed_ts,
                result_json,
            )

        logger.info("Inserted document %s for user %s", doc_id, user_id)
        return doc_id

    async def fetch_history(self, user_id: str) -> List[DocumentRecord]:
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
                DocumentRecord(
                    doc_id=row["doc_id"],
                    template_type=row["template_type"],
                    file_name=row["file_name"],
                    status=row["status"],
                    uploaded_ts=row["uploaded_ts"]
                )
            )
        return records

    async def fetch_result(self, doc_id: str) -> Optional[DocumentRecord]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT file_name,result
                FROM document_uploads
                WHERE doc_id = $1::uuid
                """,
                doc_id,
            )

        if row is None:
            return None

        result_value = json.loads(row["result"]) if row["result"] else None
        return DocumentRecord(
            file_name=row["file_name"],
            result=result_value,
        )

    async def update_status(self, doc_id: str, status: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE document_uploads
                SET status = $1, processed_ts = $2
                WHERE doc_id = $3::uuid
                """,
                status,
                self.context.get_current_timestamp(),
                doc_id,
            )
