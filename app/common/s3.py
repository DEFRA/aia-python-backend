import io
from contextlib import asynccontextmanager
from logging import getLogger

import aiobotocore.session

from app.config import config

logger = getLogger(__name__)


@asynccontextmanager
async def get_s3_client():
    session = aiobotocore.session.get_session()
    client_kwargs: dict = {
        "service_name": "s3",
        "region_name": config.aws_region
    }
    if config.aws_endpoint_url:
        client_kwargs["endpoint_url"] = config.aws_endpoint_url

    async with session.create_client(**client_kwargs) as client:
        yield client


async def upload_file_to_s3(
    file_bytes: bytes,
    s3_key: str,
    bucket: str | None = None,
) -> str:

    bucket_name = config.s3_bucket_name
    logger.info("Uploading %d bytes to s3://%s/%s", len(file_bytes), bucket_name, s3_key)

    async with get_s3_client() as client:
        await client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=io.BytesIO(file_bytes),
        )

    logger.info("Upload complete: s3://%s/%s", bucket_name, s3_key)
    return s3_key
