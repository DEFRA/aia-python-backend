from contextlib import asynccontextmanager
from typing import Optional

import aiobotocore.session

from ..config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class S3Service:
    @asynccontextmanager
    async def _get_client(self):
        session = aiobotocore.session.get_session()
        client_kwargs: dict = {
            "service_name": "s3",
            "region_name": config.aws.region,
        }
        # Only include credentials if provided (development mode); production uses IAM role
        if config.aws.access_key_id:
            client_kwargs["aws_access_key_id"] = config.aws.access_key_id
        if config.aws.secret_access_key:
            client_kwargs["aws_secret_access_key"] = config.aws.secret_access_key
        if config.aws.session_token:
            client_kwargs["aws_session_token"] = config.aws.session_token
        if config.aws.endpoint_url:
            client_kwargs["endpoint_url"] = config.aws.endpoint_url

        async with session.create_client(**client_kwargs) as client:
            yield client

    async def download_file(
        self,
        s3_key: str,
        bucket: Optional[str] = None,
    ) -> bytes:
        bucket_name = bucket or config.s3.bucket_name
        logger.info("Downloading s3://%s/%s", bucket_name, s3_key)

        async with self._get_client() as client:
            response = await client.get_object(Bucket=bucket_name, Key=s3_key)
            async with response["Body"] as stream:
                return await stream.read()
