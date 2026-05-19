from typing import Any, List, Optional

from models.cost_usage_record import (
    AgentTokenUsage,
    CostUsageDocument,
    CostUsageResponse,
    CostUsageSummary,
    Pagination,
)
from repositories.cost_usage_repository import CostUsageRepository
from utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_CURRENCY = "USD"


class CostUsageService:
    def __init__(self, repo: CostUsageRepository):
        self.repo = repo

    async def fetch_cost_usage(
        self, user_id: str, page: int = 1, limit: int = 10
    ) -> CostUsageResponse:
        page = max(page, 1)
        limit = max(limit, 1)
        rows = await self.repo.fetch_all_cost_usage(user_id)

        documents = _group_rows_into_documents(rows)
        summary = _build_summary(rows, total_documents=len(documents))

        offset = (page - 1) * limit
        page_documents = documents[offset : offset + limit]

        return CostUsageResponse(
            costUsage=page_documents,
            pagination=_build_pagination(page, limit, total=len(documents)),
            summary=summary,
        )

    async def fetch_cost_usage_by_doc(
        self, doc_id: str, user_id: str
    ) -> Optional[CostUsageDocument]:
        rows = await self.repo.fetch_cost_usage_by_doc(doc_id, user_id)
        if not rows:
            return None
        return _build_document(rows)


def _group_rows_into_documents(rows: List[Any]) -> List[CostUsageDocument]:
    """Group rows by doc_id, preserving the order returned by the query."""
    documents: List[CostUsageDocument] = []
    current_doc_id: Optional[str] = None
    current_rows: List[Any] = []
    for row in rows:
        if row["doc_id"] != current_doc_id:
            if current_rows:
                documents.append(_build_document(current_rows))
            current_doc_id = row["doc_id"]
            current_rows = [row]
        else:
            current_rows.append(row)
    if current_rows:
        documents.append(_build_document(current_rows))
    return documents


def _build_document(rows: List[Any]) -> CostUsageDocument:
    first = rows[0]
    agents = [
        AgentTokenUsage(
            name=row["agent_name"],
            inputTokens=row["input_tokens"],
            outputTokens=row["output_tokens"],
        )
        for row in rows
    ]
    total_cost = sum(float(row["total_cost_usd"]) for row in rows)
    return CostUsageDocument(
        doc_id=first["doc_id"],
        file_name=first["file_name"],
        uploadedAt=first["uploaded_ts"],
        agents=agents,
        totalCost=round(total_cost, 4),
        currency=_DEFAULT_CURRENCY,
    )


def _build_summary(rows: List[Any], total_documents: int) -> CostUsageSummary:
    total_input = 0
    total_output = 0
    total_cost = 0.0
    for row in rows:
        total_input += int(row["input_tokens"])
        total_output += int(row["output_tokens"])
        total_cost += float(row["total_cost_usd"])
    return CostUsageSummary(
        totalCost=round(total_cost, 4),
        currency=_DEFAULT_CURRENCY,
        totalDocuments=total_documents,
        totalInputTokens=total_input,
        totalOutputTokens=total_output,
        totalTokens=total_input + total_output,
    )


def _build_pagination(page: int, limit: int, total: int) -> Pagination:
    total_pages = (total + limit - 1) // limit if total > 0 else 0
    has_next = page < total_pages
    has_previous = page > 1 and total_pages > 0
    return Pagination(
        page=page,
        limit=limit,
        total=total,
        totalPages=total_pages,
        hasNext=has_next,
        hasPrevious=has_previous,
        nextPage=page + 1 if has_next else None,
        previousPage=page - 1 if has_previous else None,
    )

