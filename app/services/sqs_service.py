import json
from contextlib import asynccontextmanager
import aiobotocore.session

from app.core.config import config
from app.utils.logger import get_logger

logger = get_logger(__name__)

class SQSService:
    @asynccontextmanager
    async def _get_client(self):
        session = aiobotocore.session.get_session()
        client_kwargs: dict = {
            "service_name": "sqs",
            "region_name": config.aws.region,
            "aws_access_key_id": config.aws.access_key_id,
            "aws_secret_access_key": config.aws.secret_access_key,
        }
        if config.aws.endpoint_url:
            client_kwargs["endpoint_url"] = config.aws.endpoint_url

        async with session.create_client(**client_kwargs) as client:
            yield client

    async def send_task(self, doc_id: str, extracted_text: str) -> str:
        """Pushes the extraction payload to the SQS queue."""
        payload = {
            "docId": doc_id,
            "extractedText": extracted_text
        }
        
        logger.info("Sending task to SQS for docID: %s", doc_id)
        
        async with self._get_client() as client:
            response = await client.send_message(
                QueueUrl=config.sqs.task_queue_url,
                MessageBody=json.dumps(payload)
            )
            
        message_id = response.get("MessageId")
        logger.info("Task sent successfully. MessageId: %s", message_id)
        return message_id
