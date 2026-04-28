import httpx

from app.core.config import config
from app.utils.logger import get_logger

logger = get_logger(__name__)


class OrchestratorService:
    """Fire-and-forget HTTP client that triggers the Orchestrator after a successful S3 upload."""

    async def trigger(self, document_id: str, s3_key: str, template_type: str) -> None:
        url = f"{config.orchestrator.url}/orchestrate"
        payload = {
            "document_id": document_id,
            "s3_key": s3_key,
            "template_type": template_type,
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url, json=payload)
            if response.status_code != 202:
                logger.warning(
                    "Orchestrator returned unexpected status=%d for doc_id=%s",
                    response.status_code,
                    document_id,
                )
            else:
                logger.info("Orchestrator triggered for doc_id=%s", document_id)
        except httpx.ConnectError:
            logger.warning(
                "Orchestrator unavailable at %s — doc_id=%s stays PROCESSING until retry",
                url,
                document_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to reach orchestrator for doc_id=%s: %s", document_id, exc
            )
