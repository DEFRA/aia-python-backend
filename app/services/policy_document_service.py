from typing import Optional

from app.models.policy_document import (
    PolicyDocumentListResponse,
    PolicyDocumentRecord,
    PolicyDocumentUpdateRequest,
)
from app.repositories.policy_document_repository import PolicyDocumentRepository


class PolicyDocumentService:
    def __init__(self, repo: PolicyDocumentRepository):
        self.repo = repo

    async def fetch_policy_documents(
        self, page: int = 1, limit: int = 20
    ) -> PolicyDocumentListResponse:
        safe_page = max(1, page)
        safe_limit = max(1, limit)
        records, total = await self.repo.fetch_policy_documents(
            page=safe_page,
            limit=safe_limit,
        )
        return PolicyDocumentListResponse(
            documents=records,
            total=total,
            page=safe_page,
            limit=safe_limit,
        )

    async def fetch_policy_document_by_url_id(
        self, url_id: int
    ) -> Optional[PolicyDocumentRecord]:
        return await self.repo.fetch_policy_document_by_url_id(url_id)

    async def update_policy_document_by_url_id(
        self, url_id: int, request: PolicyDocumentUpdateRequest
    ) -> Optional[PolicyDocumentRecord]:
        return await self.repo.update_policy_document_by_url_id(url_id, request)
