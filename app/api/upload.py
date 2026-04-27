from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status

from app.models.document_record import DocumentRecord
from app.models.upload_request import UploadRequest
from app.models.upload_response import UploadResponse
from app.models.history_record import HistoryRecord
from app.models.result_record import ResultRecord
from app.services.upload_service import UploadService
from app.core.dependencies import get_upload_service, verify_auth
from app.utils.logger import get_logger
from app.core.messages import messages

router = APIRouter(prefix="/api", tags=["upload"])
logger = get_logger(__name__)
logger = get_logger(__name__)


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
    # Auth + Service
    # Auth + Service
    auth: dict = Depends(verify_auth),
    service: UploadService = Depends(get_upload_service),
    service: UploadService = Depends(get_upload_service),
) -> UploadResponse:

    user_id = auth["user_id"]
    logger.info("Upload request for userId=%s fileName=%s", user_id, fileName)

    upload_request = UploadRequest(
        templateType=templateType,
        fileName=fileName,
    )

    try:
        doc_id = await service.process_upload_request(upload_request, user_id)
        doc_id = await service.process_upload_request(upload_request, user_id)
    except Exception as exc:
        logger.exception("DB insert failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=messages.DOC_METADATA_SAVE_FAILED,
            detail=messages.DOC_METADATA_SAVE_FAILED,
        ) from exc

    if not doc_id:
        logger.warning("Duplicate file: userId=%s fileName=%s", user_id, fileName)
        return UploadResponse(
            docId="",
            statusCode=status.HTTP_400_BAD_REQUEST,
            errorMessage=messages.FILE_ALREADY_UPLOADED.format(file_name=fileName, user_id=user_id),
        )

    s3_key = service.get_s3_key(doc_id, fileName)

    # --- Background S3 Upload ---
    file_bytes = await file.read()
    background_tasks.add_task(service.process_background_upload, file_bytes, s3_key, doc_id)
    background_tasks.add_task(service.process_background_upload, file_bytes, s3_key, doc_id)

    return UploadResponse(docId=doc_id, statusCode=status.HTTP_200_OK)


@router.get(
    "/fetchUploadHistory",
    response_model=List[HistoryRecord],
    summary="Fetch upload history for a user",
)
async def fetch_upload_history(
    auth: dict = Depends(verify_auth),
    service: UploadService = Depends(get_upload_service),
) -> List[HistoryRecord]:
   
    user_id = auth["user_id"]
    logger.info("Fetching upload history for UserId=%s", user_id)
    records = await service.fetch_history(user_id)
    records = await service.fetch_history(user_id)
    return records


@router.get(
    "/result",
    response_model=ResultRecord,
    summary="Fetch result for a specific document",
)
async def get_result(
    docID: str,
    auth: dict = Depends(verify_auth),
    service: UploadService = Depends(get_upload_service),
) -> ResultRecord:

    logger.info("Fetching result for docID=%s", docID)
    record = await service.fetch_result(docID)
    record = await service.fetch_result(docID)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=messages.DOC_NOT_FOUND.format(doc_id=docID),
            detail=messages.DOC_NOT_FOUND.format(doc_id=docID),
        )
    return record
