from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_policy_document_service, verify_auth
from app.models.policy_document import (
    PolicyDocumentListResponse,
    PolicyDocumentOptionsResponse,
    PolicyDocumentRecord,
    PolicyDocumentUpdateRequest,
)
from app.services.policy_document_service import PolicyDocumentService

router = APIRouter(prefix="/policy-documents", tags=["policy-documents"])


@router.get(
    "/options",
    response_model=PolicyDocumentOptionsResponse,
    summary="Fetch policy document source/category options",
)
async def fetch_policy_document_options(
    _auth: dict = Depends(verify_auth),
    service: PolicyDocumentService = Depends(get_policy_document_service),
) -> PolicyDocumentOptionsResponse:
    return await service.fetch_policy_document_options()


@router.get(
    "",
    response_model=PolicyDocumentListResponse,
    summary="Fetch paginated policy documents",
)
async def fetch_policy_documents(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    _auth: dict = Depends(verify_auth),
    service: PolicyDocumentService = Depends(get_policy_document_service),
) -> PolicyDocumentListResponse:
    return await service.fetch_policy_documents(page=page, limit=limit)


@router.get(
    "/{url_id}",
    response_model=PolicyDocumentRecord,
    summary="Fetch a policy document by url_id",
)
async def fetch_policy_document_by_url_id(
    url_id: int,
    _auth: dict = Depends(verify_auth),
    service: PolicyDocumentService = Depends(get_policy_document_service),
) -> PolicyDocumentRecord:
    document = await service.fetch_policy_document_by_url_id(url_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy document '{url_id}' not found.",
        )
    return document


@router.put(
    "/{url_id}",
    response_model=PolicyDocumentRecord,
    summary="Update a policy document by url_id",
)
async def update_policy_document_by_url_id(
    url_id: int,
    request: PolicyDocumentUpdateRequest,
    _auth: dict = Depends(verify_auth),
    service: PolicyDocumentService = Depends(get_policy_document_service),
) -> PolicyDocumentRecord:
    try:
        updated = await service.update_policy_document_by_url_id(url_id, request)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy document '{url_id}' not found.",
        )
    return updated
