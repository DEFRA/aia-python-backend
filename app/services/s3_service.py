import io
from contextlib import asynccontextmanager
from typing import Optional
import aiobotocore.session

from app.core.config import config
from app.utils.logger import get_logger

logger = get_logger(__name__)

class S3Service:
    @asynccontextmanager
    async def _get_client(self):
        session = aiobotocore.session.get_session()
        client_kwargs: dict = {
            "service_name": "s3",
            "region_name": config.aws.region,
            "aws_access_key_id": config.aws.access_key_id,
            "aws_secret_access_key": config.aws.secret_access_key,
        }
        if config.aws.endpoint_url:
            client_kwargs["endpoint_url"] = config.aws.endpoint_url

        async with session.create_client(**client_kwargs) as client:
            yield client

    async def upload_file(
        self,
        file_bytes: bytes,
        s3_key: str,
        bucket: Optional[str] = None,
    ) -> str:
        bucket_name = bucket or config.s3.bucket_name
        logger.info("Uploading %d bytes to s3://%s/%s", len(file_bytes), bucket_name, s3_key)

        async with self._get_client() as client:
            await client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=io.BytesIO(file_bytes),
            )

        logger.info("Upload complete: s3://%s/%s", bucket_name, s3_key)
        return s3_key

    async def download_file(
        self,
        s3_key: str,
        bucket: Optional[str] = None,
    ) -> bytes:
        """Downloads a file from S3 and returns its bytes."""
        bucket_name = bucket or config.s3.bucket_name
        logger.info("Downloading s3://%s/%s", bucket_name, s3_key)

        async with self._get_client() as client:
            response = await client.get_object(Bucket=bucket_name, Key=s3_key)
            async with response["Body"] as stream:
                return await stream.read()
