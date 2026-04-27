import io
from typing import List

import docx

from app.core.enums import DocumentStatus
from app.repositories.document_repository import DocumentRepository
from app.services.s3_service import S3Service
from app.services.sqs_service import SQSService
from app.utils.logger import get_logger

logger = get_logger(__name__)


class IngestorService:
    def __init__(
        self,
        repo: DocumentRepository,
        s3_service: S3Service,
        sqs_service: SQSService,
    ):
        self.repo = repo
        self.s3_service = s3_service
        self.sqs_service = sqs_service

    def extract_text_from_docx(self, file_bytes: bytes) -> str:
        doc = docx.Document(io.BytesIO(file_bytes))
        return "\n".join(para.text for para in doc.paragraphs)

    async def process_batch(self, limit: int = 5) -> int:
        records = await self.repo.claim_pending_documents(limit)
        if not records:
            return 0

        processed_count = 0
        for record in records:
            try:
                logger.info("Processing document: %s (%s)", record.doc_id, record.file_name)
                s3_key = f"{record.doc_id}_{record.file_name}"
                file_bytes = await self.s3_service.download_file(s3_key)
                text = self.extract_text_from_docx(file_bytes)
                await self.sqs_service.send_task(record.doc_id, text)
                processed_count += 1
            except Exception as exc:
                logger.exception("Failed to ingest document %s: %s", record.doc_id, exc)
                await self.repo.update_status(
                    record.doc_id,
                    DocumentStatus.ERROR.value,
                    error_message=str(exc),
                )
        return processed_count
