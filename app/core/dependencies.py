import asyncpg
from fastapi import Depends, HTTPException, Request, status

from app.utils.postgres import get_db_pool
from app.utils.auth import AuthService
from app.utils.app_context import AppContext
from app.repositories.document_repository import DocumentRepository
from app.services.s3_service import S3Service
from app.services.upload_service import UploadService
from app.core.config import config
from app.utils.logger import get_logger
from app.core.messages import messages

logger = get_logger(__name__)

async def verify_auth(request: Request) -> dict:
    """
    FastAPI dependency to verify JWT and identity headers.
    """
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
            detail=messages.MISSING_USER_ID_HEADER.format(header=config.auth.user_id_header)
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
            status_code=status.HTTP_403_FORBIDDEN,
            detail=messages.AUTH_IDENTITY_MISMATCH
        )

    return {"user_id": verified_user_id}

def get_app_context() -> AppContext:
    return AppContext()

def get_document_repository(
    pool: asyncpg.Pool = Depends(get_db_pool),
    context: AppContext = Depends(get_app_context)
) -> DocumentRepository:
    return DocumentRepository(pool, context)

def get_s3_service() -> S3Service:
    return S3Service()

def get_upload_service(
    repo: DocumentRepository = Depends(get_document_repository),
    s3_service: S3Service = Depends(get_s3_service),
    context: AppContext = Depends(get_app_context)
) -> UploadService:
    return UploadService(repo, s3_service, context)
