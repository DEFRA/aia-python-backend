from typing import List, Optional, Tuple

from app.core.enums import DocumentStatus
from app.models.history_record import HistoryRecord
from app.models.result_record import ResultRecord
from app.models.upload_request import UploadRequest
from app.repositories.document_repository import DocumentRepository
from app.services.s3_service import S3Service
from app.utils.app_context import AppContext
from app.utils.logger import get_logger

logger = get_logger(__name__)


class UploadService:
    def __init__(self, repo: DocumentRepository, s3_service: S3Service, context: AppContext):
        self.repo = repo
        self.s3_service = s3_service
        self.context = context

    async def process_upload_request(self, request: UploadRequest, user_id: str) -> Optional[str]:
        is_duplicate = await self.repo.check_duplicate(user_id, request.fileName)
        if is_duplicate:
            return None
        doc_id = self.context.generate_uuid()
        await self.repo.insert_document(request, doc_id, user_id)
        return doc_id

    def get_s3_key(self, doc_id: str, file_name: str) -> str:
        return f"{doc_id}_{file_name}"

    async def get_processing_document_ids(self, user_id: str) -> list[str]:
        return await self.repo.get_processing_document_ids(user_id)

    async def fetch_history(
        self, user_id: str, page: int = 1, limit: int = 20
    ) -> Tuple[List[HistoryRecord], int]:
        return await self.repo.fetch_history(user_id, page=page, limit=limit)

    async def fetch_result(self, doc_id: str, user_id: str) -> Optional[ResultRecord]:
        return await self.repo.fetch_result(doc_id, user_id)

    async def process_background_upload(
        self, file_bytes: bytes, s3_key: str, doc_id: str
    ) -> None:
        try:
            await self.s3_service.upload_file(file_bytes, s3_key)
        except Exception as exc:
            logger.exception("Background S3 upload failed for doc_id=%s: %s", doc_id, exc)
            await self.repo.update_status(
                doc_id,
                DocumentStatus.ERROR.value,
                error_message="File upload to S3 failed.",
            )
