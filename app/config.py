from pydantic import HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict()
    python_env: str | None = None
    host: str = "127.0.0.1"
    port: int = 8086
    log_config: str | None = None
    aws_endpoint_url: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    http_proxy: HttpUrl | None = None
    enable_metrics: bool = False
    tracing_header: str = "x-cdp-request-id"
    # PostgreSQL
    postgres_uri: str | None = None
    # S3
    s3_bucket_name: str = "docsupload"
    aws_region: str = "eu-west-2"
    # Auth
    user_id_header: str = "X-User-Id"
    jwt_secret: str = "dev-secret-key-override-in-production"


config = AppConfig()
