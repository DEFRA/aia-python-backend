from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class StatusRecord(BaseModel):
    documentId: str
    status: str
    errorMessage: Optional[str] = None
    createdAt: datetime
    completedAt: Optional[datetime] = None
