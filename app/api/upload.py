import uuid
from logging import getLogger
from typing import List

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status

from app.utils.postgres import get_db_pool
from app.utils.s3 import upload_file_to_s3
from app.config import config
from app.api.upload_auth import verify_auth
from app.models.upload_models import DocumentRecord, UploadRequest, UploadResponse
from app.services import upload_service as service

router = APIRouter(prefix="/api", tags=["upload"])
logger = getLogger(__name__)


@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="Initialize an upload, save metadata, and return an S3 presigned URL",
)
async def upload_document(
    background_tasks: BackgroundTasks,
    # Metadata fields (multipart form)
    templateType: str = Form(...),
    fileName: str = Form(...),
    # Binary file
    file: UploadFile = File(...),
    # Auth + DB
    auth: dict = Depends(verify_auth),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> UploadResponse:

    user_id = auth["user_id"]
    logger.info("Upload request for userId=%s fileName=%s", user_id, fileName)

    # --- Duplicate filename check ---
    is_duplicate = await service.check_duplicate(pool, user_id, fileName)
    if is_duplicate:
        logger.warning("Duplicate file: userId=%s fileName=%s", user_id, fileName)
        return UploadResponse(
            docId="",
            statusCode=status.HTTP_400_BAD_REQUEST,
            errorMessage=f"A file named '{fileName}' has already been uploaded by user '{user_id}'.",
        )

    doc_id = str(uuid.uuid4())
    s3_key = f"{user_id}/{doc_id}_{fileName}"

    # --- Build Request Model ---
    upload_request = UploadRequest(
        templateType=templateType,
        fileName=fileName,
    )

    # --- Insert into PostgreSQL ---
    try:
        await service.insert_document(pool, upload_request, doc_id, user_id)
    except Exception as exc:
        logger.exception("DB insert failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record document metadata.",
        ) from exc

    # --- Background S3 Upload ---
    file_bytes = await file.read()
    background_tasks.add_task(upload_file_to_s3, file_bytes, s3_key)

    return UploadResponse(docId=doc_id, statusCode=status.HTTP_200_OK)


@router.get(
    "/fetchUploadHistory",
    response_model=List[DocumentRecord],
    summary="Fetch upload history for a user",
)
async def fetch_upload_history(
    auth: dict = Depends(verify_auth),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> List[DocumentRecord]:
   
    user_id = auth["user_id"]
    logger.info("Fetching upload history for UserId=%s", user_id)
    records = await service.fetch_history(pool, user_id)
    return records


@router.get(
    "/result",
    response_model=DocumentRecord,
    summary="Fetch result for a specific document",
)
async def get_result(
    docID: str,
    auth: dict = Depends(verify_auth),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> DocumentRecord:

    logger.info("Fetching result for docID=%s", docID)
    record = await service.fetch_result(pool, docID)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with docID '{docID}' not found.",
        )
    return record
