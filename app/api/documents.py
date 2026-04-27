from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status

from app.core.enums import DocumentStatus
from app.models.result_record import ResultRecord
from app.models.upload_request import UploadRequest
from app.models.upload_response import UploadResponse
from app.services.upload_service import UploadService
from app.core.dependencies import get_upload_service, verify_auth
from app.core.messages import messages
from app.utils.logger import get_logger

router = APIRouter(prefix="/documents", tags=["documents"])
logger = get_logger(__name__)

_MAX_HISTORY_LIMIT = 100


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document for AI assessment",
)
async def upload_document(
    background_tasks: BackgroundTasks,
    templateType: str = Form(...),
    fileName: str = Form(...),
    file: UploadFile = File(...),
    auth: dict = Depends(verify_auth),
    service: UploadService = Depends(get_upload_service),
) -> UploadResponse:
    user_id = auth["user_id"]
    logger.info("Upload request userId=%s fileName=%s", user_id, fileName)

    upload_request = UploadRequest(templateType=templateType, fileName=fileName)

    try:
        doc_id = await service.process_upload_request(upload_request, user_id)
    except Exception as exc:
        logger.exception("DB insert failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=messages.DOC_METADATA_SAVE_FAILED,
        ) from exc

    if not doc_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=messages.FILE_ALREADY_UPLOADED.format(file_name=fileName, user_id=user_id),
        )

    s3_key = service.get_s3_key(doc_id, fileName)
    file_bytes = await file.read()
    background_tasks.add_task(
        service.process_background_upload, file_bytes, s3_key, doc_id, templateType
    )

    return UploadResponse(documentId=doc_id, status=DocumentStatus.PROCESSING.value)


# NOTE: /status must be registered before /{document_id} so FastAPI does not
# treat the literal string "status" as a document_id path parameter.
@router.get(
    "/status",
    summary="List documentIds still being processed for the authenticated user",
)
async def get_processing_status(
    auth: dict = Depends(verify_auth),
    service: UploadService = Depends(get_upload_service),
) -> dict:
    user_id = auth["user_id"]
    ids = await service.get_processing_document_ids(user_id)
    logger.debug("Processing status userId=%s count=%d", user_id, len(ids))
    return {"processingDocumentIds": ids}


@router.get(
    "",
    summary="Fetch paginated upload history for the authenticated user",
)
async def fetch_upload_history(
    page: int = 1,
    limit: int = 20,
    auth: dict = Depends(verify_auth),
    service: UploadService = Depends(get_upload_service),
) -> dict:
    user_id = auth["user_id"]
    if limit > _MAX_HISTORY_LIMIT:
        limit = _MAX_HISTORY_LIMIT
    logger.info("History request userId=%s page=%d limit=%d", user_id, page, limit)
    records, total = await service.fetch_history(user_id, page=page, limit=limit)
    return {"documents": [r.model_dump() for r in records], "total": total, "page": page, "limit": limit}


@router.get(
    "/{document_id}",
    response_model=ResultRecord,
    summary="Fetch metadata and status for a specific document",
)
async def get_document(
    document_id: str,
    auth: dict = Depends(verify_auth),
    service: UploadService = Depends(get_upload_service),
) -> ResultRecord:
    user_id = auth["user_id"]
    logger.debug("Document fetch userId=%s documentId=%s", user_id, document_id)
    record = await service.fetch_result(document_id, user_id)
    if record is None:
        logger.warning("Document not found userId=%s documentId=%s", user_id, document_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=messages.DOC_NOT_FOUND.format(doc_id=document_id),
        )
    return record
