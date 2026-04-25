from typing import List, Optional

from app.models.document_record import DocumentRecord
from app.models.upload_request import UploadRequest
from app.models.history_record import HistoryRecord
from app.models.result_record import ResultRecord
from app.core.enums import UploadStatus
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
        """
        Validates duplicate, generates ID, and persists initial metadata.
        Returns the new doc_id if successful, or None if duplicate.
        """
        is_duplicate = await self.repo.check_duplicate(user_id, request.fileName)
        if is_duplicate:
            return None

        doc_id = self.context.generate_uuid()
        await self.repo.insert_document(request, doc_id, user_id)
        return doc_id

    def get_s3_key(self, doc_id: str, file_name: str) -> str:
        """Encapsulates the S3 key naming convention."""
        return f"{doc_id}_{file_name}"

    async def fetch_history(self, user_id: str) -> List[HistoryRecord]:
        return await self.repo.fetch_history(user_id)

    async def fetch_result(self, doc_id: str) -> Optional[ResultRecord]:
        return await self.repo.fetch_result(doc_id)

    async def process_background_upload(self, file_bytes: bytes, s3_key: str, doc_id: str) -> None:
        try:
            await self.s3_service.upload_file(file_bytes, s3_key)
            status = UploadStatus.ANALYSING.value
        except Exception as exc:
            logger.exception("Background upload failed for doc_id=%s: %s", doc_id, exc)
            status = UploadStatus.FAILED.value
        
        await self.repo.update_status(doc_id, status)
