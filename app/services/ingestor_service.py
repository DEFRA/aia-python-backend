import io
import docx
from typing import List

from app.repositories.document_repository import DocumentRepository
from app.services.s3_service import S3Service
from app.services.sqs_service import SQSService
from app.core.enums import UploadStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)

class IngestorService:
    def __init__(
        self, 
        repo: DocumentRepository, 
        s3_service: S3Service, 
        sqs_service: SQSService
    ):
        self.repo = repo
        self.s3_service = s3_service
        self.sqs_service = sqs_service

    def extract_text_from_docx(self, file_bytes: bytes) -> str:
        """Extracts all paragraph text from a DOCX file."""
        doc = docx.Document(io.BytesIO(file_bytes))
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        return "\n".join(full_text)

    async def process_batch(self, limit: int = 5) -> int:
        """
        Polls for pending documents, extracts text, and queues them.
        Returns the number of documents successfully processed.
        """
        records = await self.repo.claim_pending_documents(limit)
        if not records:
            return 0

        processed_count = 0
        for record in records:
            try:
                logger.info("Processing document: %s (%s)", record.doc_id, record.file_name)
                
                # 1. Download from S3
                s3_key = f"{record.doc_id}_{record.file_name}"
                file_bytes = await self.s3_service.download_file(s3_key)
                
                # 2. Extract Text
                text = self.extract_text_from_docx(file_bytes)
                
                # 3. Push to SQS
                await self.sqs_service.send_task(record.doc_id, text)
                
                # 4. Update DB to INGESTED
                await self.repo.update_status(record.doc_id, UploadStatus.INGESTED.value)
                processed_count += 1
                
            except Exception as e:
                logger.exception("Failed to ingest document %s: %s", record.doc_id, str(e))
                await self.repo.update_status(record.doc_id, UploadStatus.FAILED.value)

        return processed_count
