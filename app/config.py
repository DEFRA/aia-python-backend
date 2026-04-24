from typing import Optional
from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """
    High-Standard Configuration Root.
    Loads flat environment variables and organizes them into logical groups.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # --- Core App Settings ---
    env: Optional[str] = Field(None, alias="PYTHON_ENV")
    host: str = "127.0.0.1"
    port: int = 8086
    log_config: Optional[str] = Field(None, alias="LOG_CONFIG")
    http_proxy: Optional[HttpUrl] = Field(None, alias="HTTP_PROXY")
    tracing_header: str = Field("x-cdp-request-id", alias="TRACING_HEADER")

    # --- Database Settings ---
    postgres_uri: str = Field(..., alias="POSTGRES_URI")

    # --- AWS / S3 Settings ---
    aws_region: str = Field("eu-west-2", alias="AWS_REGION")
    aws_endpoint_url: Optional[str] = Field(None, alias="AWS_ENDPOINT_URL")
    aws_access_key_id: Optional[str] = Field(None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[str] = Field(None, alias="AWS_SECRET_ACCESS_KEY")
    s3_bucket_name: str = Field("docsupload", alias="S3_BUCKET_NAME")

    # --- Auth Settings ---
    jwt_secret: str = Field(..., alias="JWT_SECRET")
    user_id_header: str = Field("X-User-Id", alias="USER_ID_HEADER")

    @property
    def app(self):
        class _App:
            env = self.env
            host = self.host
            port = self.port
            log_config = self.log_config
            http_proxy = self.http_proxy
            tracing_header = self.tracing_header
        return _App()

    @property
    def db(self):
        class _DB:
            uri = self.postgres_uri
        return _DB()

    @property
    def aws(self):
        class _AWS:
            region = self.aws_region
            endpoint_url = self.aws_endpoint_url
            access_key_id = self.aws_access_key_id
            secret_access_key = self.aws_secret_access_key
        return _AWS()

    @property
    def s3(self):
        class _S3:
            bucket_name = self.s3_bucket_name
        return _S3()

    @property
    def auth(self):
        class _Auth:
            jwt_secret = self.jwt_secret
            user_id_header = self.user_id_header
        return _Auth()


# Global config instance
config = AppConfig()
