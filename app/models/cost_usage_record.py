from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class CostUsageRecord(BaseModel):
    documentId: str
    fileName: str
    agentName: str
    inputTokens: int
    outputTokens: int
    unitCost: float
    uploadedTs: datetime
    processedTs: Optional[datetime] = None
