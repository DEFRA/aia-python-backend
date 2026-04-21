import json
from datetime import datetime
from logging import getLogger
import asyncpg
from asyncpg import UniqueViolationError

from app.upload.models import DocumentRecord, UploadRequest

logger = getLogger(__name__)


async def check_duplicate(pool: asyncpg.Pool, user_id: str, file_name: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT doc_id FROM document_uploads WHERE user_id = $1 AND file_name = $2",
            user_id,
            file_name,
        )
    return row is not None


async def insert_document(
    pool: asyncpg.Pool,
    request: UploadRequest,
    doc_id: str,
    user_id: str,
) -> str:
   
    uploaded_ts = datetime.utcnow().astimezone()
    processed_ts = None
    status = "Analysing"
    result_json = None

    async with pool.acquire() as conn:
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


async def fetch_history(pool: asyncpg.Pool, user_id: str) -> list[DocumentRecord]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT doc_id::text, template_type, user_id, file_name, status,
                   uploaded_ts, processed_ts, result
            FROM document_uploads
            WHERE user_id = $1
            ORDER BY uploaded_ts DESC
            """,
            user_id,
        )

    records = []
    for row in rows:
        result_value = json.loads(row["result"]) if row["result"] else None
        records.append(
            DocumentRecord(
                doc_id=row["doc_id"],
                template_type=row["template_type"],
                user_id=row["user_id"],
                file_name=row["file_name"],
                status=row["status"],
                uploaded_ts=row["uploaded_ts"],
                processed_ts=row["processed_ts"],
                result=result_value,
            )
        )
    return records


async def fetch_result(pool: asyncpg.Pool, doc_id: str) -> DocumentRecord | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT doc_id::text, template_type, user_id, file_name, status,
                   uploaded_ts, processed_ts, result
            FROM document_uploads
            WHERE doc_id = $1::uuid
            """,
            doc_id,
        )

    if row is None:
        return None

    result_value = json.loads(row["result"]) if row["result"] else None
    return DocumentRecord(
        doc_id=row["doc_id"],
        template_type=row["template_type"],
        user_id=row["user_id"],
        file_name=row["file_name"],
        status=row["status"],
        uploaded_ts=row["uploaded_ts"],
        processed_ts=row["processed_ts"],
        result=result_value,
    )
