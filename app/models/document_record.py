from datetime import datetime
from typing import Dict, Optional
from pydantic import BaseModel


class DocumentRecord(BaseModel):
    doc_id: Optional[str] = None
    template_type: Optional[str] = None
    user_id: Optional[str] = None
    file_name: str
    status: Optional[str] = None
    uploaded_ts: Optional[datetime] = None
    processed_ts: Optional[datetime] = None
    result: Optional[Dict] = None
