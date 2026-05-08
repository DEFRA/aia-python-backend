from typing import List

from fastapi import APIRouter, Depends

from app.core.dependencies import get_cost_usage_service, verify_auth
from app.models.cost_usage_record import CostUsageRecord
from app.services.cost_usage_service import CostUsageService
from app.utils.logger import get_logger

router = APIRouter(prefix="/cost-usage", tags=["cost-usage"])
logger = get_logger(__name__)


@router.get(
    "",
    response_model=List[CostUsageRecord],
    summary="Fetch cost usage records for the authenticated user",
)
async def fetch_cost_usage(
    auth: dict = Depends(verify_auth),
    service: CostUsageService = Depends(get_cost_usage_service),
) -> List[CostUsageRecord]:
    user_id = auth["user_id"]
    records = await service.fetch_cost_usage(user_id)
    logger.info("Cost usage userId=%s count=%d", user_id, len(records))
    return records


@router.get(
    "/{document_id}",
    response_model=List[CostUsageRecord],
    summary="Fetch cost usage records for a specific document",
)
async def fetch_cost_usage_by_document(
    document_id: str,
    auth: dict = Depends(verify_auth),
    service: CostUsageService = Depends(get_cost_usage_service),
) -> List[CostUsageRecord]:
    user_id = auth["user_id"]
    records = await service.fetch_cost_usage_by_doc(document_id, user_id)
    logger.debug(
        "Cost usage by document userId=%s documentId=%s count=%d",
        user_id,
        document_id,
        len(records),
    )
    return records
