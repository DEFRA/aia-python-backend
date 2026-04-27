from datetime import datetime
from typing import Optional
from pydantic import BaseModel

class HistoryRecord(BaseModel):
    doc_id: str
    template_type: str
    file_name: str
    status: str
    uploaded_ts: datetime
