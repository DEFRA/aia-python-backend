from typing import List

from app.models.cost_usage_record import CostUsageRecord
from app.repositories.cost_usage_repository import CostUsageRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CostUsageService:
    def __init__(self, repo: CostUsageRepository):
        self.repo = repo

    async def fetch_cost_usage(self, user_id: str) -> List[CostUsageRecord]:
        return await self.repo.fetch_cost_usage(user_id)

    async def fetch_cost_usage_by_doc(
        self, doc_id: str, user_id: str
    ) -> List[CostUsageRecord]:
        return await self.repo.fetch_cost_usage_by_doc(doc_id, user_id)
