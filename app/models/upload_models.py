from datetime import datetime
from typing import Dict, Optional

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
    processed_ts: Optional[datetime] = None
    result: Optional[Dict] = None
