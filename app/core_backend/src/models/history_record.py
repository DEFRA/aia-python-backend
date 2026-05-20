from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class HistoryRecord(BaseModel):
    documentId: str
    originalFilename: str
    templateType: str
    status: str
    createdAt: datetime
    completedAt: Optional[datetime] = None
