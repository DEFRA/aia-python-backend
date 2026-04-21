from datetime import datetime

from pydantic import BaseModel


class UploadRequest(BaseModel):
    templateType: str
    fileName: str


class UploadResponse(BaseModel):
    docId: str
    statusCode: int
    errorMessage: str = ""


class DocumentRecord(BaseModel):
    doc_id: str
    template_type: str
    user_id: str
    file_name: str
    status: str
    uploaded_ts: datetime
    processed_ts: datetime | None = None
    result: dict | None = None
