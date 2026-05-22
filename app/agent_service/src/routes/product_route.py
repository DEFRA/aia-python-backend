"""Product route placeholder for future HTTP endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/")
async def list_products() -> dict[str, str]:
    """Placeholder — returns empty response."""
    return {"status": "not_implemented"}
