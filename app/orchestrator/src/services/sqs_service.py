from contextlib import asynccontextmanager
from typing import Any

import aiobotocore.session

from ..config import config
from ..schemas.task_message import TaskMessage
from ..utils.logger import get_logger

logger = get_logger(__name__)


class SQSService:
    @asynccontextmanager
    async def _get_client(self):
        session = aiobotocore.session.get_session()
        client_kwargs: dict[str, Any] = {
            "service_name": "sqs",
            "region_name": config.aws.region,
            "aws_access_key_id": config.aws.access_key_id,
            "aws_secret_access_key": config.aws.secret_access_key,
        }
        if config.aws.session_token:
            client_kwargs["aws_session_token"] = config.aws.session_token
        if config.aws.endpoint_url:
            client_kwargs["endpoint_url"] = config.aws.endpoint_url
        async with session.create_client(**client_kwargs) as client:
            yield client

    async def send_task(self, task: TaskMessage) -> str:
        body = task.model_dump_json(by_alias=True)
        queue_url = config.sqs.task_queue_url
        logger.info(
            "Publishing task task_id=%s agent_type=%s to %s",
            task.task_id,
            task.agent_type,
            queue_url,
        )
        send_kwargs: dict[str, Any] = {
            "QueueUrl": queue_url,
            "MessageBody": body,
        }
        if queue_url.endswith(".fifo"):
            send_kwargs["MessageGroupId"] = task.document_id
            send_kwargs["MessageDeduplicationId"] = task.task_id
        async with self._get_client() as client:
            response = await client.send_message(**send_kwargs)
        message_id: str = response["MessageId"]
        logger.info("Task published message_id=%s", message_id)
        return message_id

    async def receive_messages(
        self,
        queue_url: str,
        max_messages: int = 10,
        wait_seconds: int = 20,
        visibility_timeout: int = 0,
    ) -> list[dict[str, str]]:
        kwargs: dict[str, Any] = {
            "QueueUrl": queue_url,
            "MaxNumberOfMessages": min(max_messages, 10),
            "WaitTimeSeconds": wait_seconds,
            "AttributeNames": ["All"],
        }
        if visibility_timeout > 0:
            kwargs["VisibilityTimeout"] = visibility_timeout
        async with self._get_client() as client:
            response = await client.receive_message(**kwargs)
        raw_messages = response.get("Messages", [])
        return [
            {"body": m["Body"], "receipt_handle": m["ReceiptHandle"]}
            for m in raw_messages
        ]

    async def delete_message(self, queue_url: str, receipt_handle: str) -> None:
        async with self._get_client() as client:
            await client.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
            )
