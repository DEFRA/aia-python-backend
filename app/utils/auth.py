import jwt
from app.utils.logger import get_logger
from typing import Optional, Any, Dict
from fastapi import HTTPException, status
from app.core.config import config
from app.core.messages import messages

logger = get_logger(__name__)

class AuthService:

    @classmethod
    def authorise_user(cls, sso_token: Optional[str]) -> str:
    
        if not sso_token or not sso_token.strip():
            logger.warning("Authentication failed: Missing SSO token.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=messages.MISSING_AUTH_HEADER
            )
            
        logger.info("Using provided SSO token from external source.")
        return sso_token

    @staticmethod
    def get_user_id(token: str) -> str:
        try:
            # Decode and validate the token
            # We strip the secret to avoid issues with accidental whitespace in config
            payload: Dict[Any, Any] = jwt.decode(
                token, 
                config.auth.jwt_secret.strip(), 
                algorithms=["HS256"]
            )
            
            # Extract the 'sub' (subject) claim as the unique user identifier
            user_id = payload.get("sub")
            
            if not user_id:
                logger.error("JWT payload missing 'sub' claim")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=messages.INVALID_TOKEN_MISSING_SUB
                )
            
            return str(user_id)

        except jwt.ExpiredSignatureError:
            logger.warning("JWT token has expired")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=messages.TOKEN_EXPIRED
            )
        except jwt.InvalidSignatureError:
            logger.error("JWT signature verification failed. Token provided might not match backend secret.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=messages.TOKEN_INVALID_SIGNATURE
            )
        except jwt.InvalidTokenError as e:
            logger.warning("Invalid JWT token: %s", str(e))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=messages.TOKEN_INVALID_FORMAT.format(error=str(e))
            )
        except Exception as e:
            logger.exception("Unexpected error during JWT validation")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=messages.INTERNAL_AUTH_ERROR
            )
