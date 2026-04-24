from logging import getLogger

from fastapi import HTTPException, Request, status

from app.utils.auth import AuthService
from app.config import config

logger = getLogger(__name__)


async def verify_auth(request: Request) -> dict:
    # 1. Capture the token from the standard Authorization header
    auth_header = request.headers.get("Authorization", "")
    sso_token = None
    
    if auth_header.lower().startswith("bearer "):
        sso_token = auth_header[7:].strip()
    
    # 2. Capture the claimed User ID from headers
    claimed_user_id = request.headers.get(config.auth.user_id_header)
    
    if not claimed_user_id:
        logger.warning("Authentication failed: Missing %s header", config.auth.user_id_header)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing {config.auth.user_id_header} header"
        )

    # 3. Authorize and validate token (raises 401 if missing or invalid)
    token = AuthService.authorise_user(sso_token)
    
    # 4. Resolve the Verified User ID from the token
    verified_user_id = AuthService.get_user_id(token)
    
    # 5. Identity Cross-Validation
    if claimed_user_id != verified_user_id:
        logger.error(
            "Identity Mismatch: Header UserID (%s) does not match Token Subject (%s)", 
            claimed_user_id, 
            verified_user_id
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User identity mismatch. Token does not belong to the claimed user."
        )
    
    logger.info("Authenticated and verified user_id=%s", verified_user_id)
    
    return {
        "token": token, 
        "user_id": verified_user_id
    }
