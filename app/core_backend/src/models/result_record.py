from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ResultRecord(BaseModel):
    documentId: str
    originalFilename: str
    templateType: str
    status: str
    resultMd: Optional[str] = None
    errorMessage: Optional[str] = None
    createdAt: datetime
    completedAt: Optional[datetime] = None
