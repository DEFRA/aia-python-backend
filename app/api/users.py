from fastapi import APIRouter, Depends, HTTPException, status

from app.models.user_record import UserRecord
from app.repositories.user_repository import GUEST_USER, UserRepository
from app.core.dependencies import get_user_repository, verify_auth
from app.core.messages import messages
from app.utils.logger import get_logger

router = APIRouter(prefix="/users", tags=["users"])
logger = get_logger(__name__)


@router.get(
    "/me",
    response_model=UserRecord,
    summary="Return the authenticated user's profile",
)
async def get_current_user(
    auth: dict = Depends(verify_auth),
    user_repo: UserRepository = Depends(get_user_repository),
) -> UserRecord:
    user_id = auth["user_id"]
    user = await user_repo.get_user_by_id(user_id)
    if user is None:
        logger.info("User %s not found in DB; returning guest user", user_id)
        return GUEST_USER
    return user
