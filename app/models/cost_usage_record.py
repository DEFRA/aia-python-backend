from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class AgentTokenUsage(BaseModel):
    name: str
    inputTokens: int
    outputTokens: int


class CostUsageDocument(BaseModel):
    doc_id: str
    file_name: str
    uploadedAt: datetime
    agents: List[AgentTokenUsage]
    totalCost: float
    currency: str = "USD"


class Pagination(BaseModel):
    page: int
    limit: int
    total: int
    totalPages: int
    hasNext: bool
    hasPrevious: bool
    nextPage: Optional[int] = None
    previousPage: Optional[int] = None


class CostUsageSummary(BaseModel):
    totalCost: float
    currency: str = "USD"
    totalDocuments: int
    totalInputTokens: int
    totalOutputTokens: int
    totalTokens: int


class CostUsageResponse(BaseModel):
    costUsage: List[CostUsageDocument]
    pagination: Pagination
    summary: CostUsageSummary
