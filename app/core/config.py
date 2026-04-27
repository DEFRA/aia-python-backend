from typing import Optional
from pydantic import Field, HttpUrl, BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.enums import LogLevel

class AWSConfig(BaseModel):
    region: str = "eu-west-2"
    access_key_id: str = "test"
    secret_access_key: str = "test"
    endpoint_url: Optional[str] = None

class S3Config(BaseModel):
    bucket_name: str = "docsupload"

class SQSConfig(BaseModel):
    task_queue_url: str = "http://localhost:4566/000000000000/task-queue"

class DBConfig(BaseModel):
    uri: Optional[str] = None

class AuthConfig(BaseModel):
    jwt_secret: str = "test_secret"
    user_id_header: str = "x-user-id"

class AppSettings(BaseModel):
    env: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8086
    log_config: Optional[str] = None
    log_level: LogLevel = LogLevel.INFO
    http_proxy: Optional[HttpUrl] = None
    tracing_header: str = "x-cdp-request-id"
    worker_stuck_task_timeout_minutes: int = 120

class AppConfig(BaseSettings):
    # Flattened environment mappings
    env: str = Field("dev", alias="PYTHON_ENV")
    host: str = Field("127.0.0.1", alias="HOST")
    port: int = Field(8086, alias="PORT")
    log_config: Optional[str] = Field(None, alias="LOG_CONFIG")
    log_level: LogLevel = Field(LogLevel.INFO, alias="LOG_LEVEL")
    http_proxy: Optional[HttpUrl] = Field(None, alias="HTTP_PROXY")
    tracing_header: str = Field("x-cdp-request-id", alias="TRACING_HEADER")
    worker_stuck_task_timeout_minutes: int = Field(120, alias="WORKER_STUCK_TASK_TIMEOUT_MINUTES")

    # AWS
    aws_region: str = Field("eu-west-2", alias="AWS_DEFAULT_REGION")
    aws_access_key: str = Field("test", alias="AWS_ACCESS_KEY_ID")
    aws_secret_key: str = Field("test", alias="AWS_SECRET_ACCESS_KEY")
    aws_endpoint: Optional[str] = Field(None, alias="AWS_ENDPOINT_URL")

    # S3
    s3_bucket: str = Field("docsupload", alias="S3_BUCKET_NAME")

    # SQS
    sqs_url: str = Field("http://localhost:4566/000000000000/task-queue", alias="TASK_QUEUE_URL")

    # DB
    db_uri: Optional[str] = Field(None, alias="POSTGRES_URI")

    # Auth
    jwt_secret: str = Field("test_secret", alias="JWT_SECRET")
    user_id_header: str = Field("x-user-id", alias="USER_ID_HEADER")

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # Properties to maintain compatibility with legacy app.aws, app.s3, etc.
    @property
    def app(self) -> AppSettings:
        return AppSettings(
            env=self.env, host=self.host, port=self.port,
            log_config=self.log_config, log_level=self.log_level,
            http_proxy=self.http_proxy, tracing_header=self.tracing_header,
            worker_stuck_task_timeout_minutes=self.worker_stuck_task_timeout_minutes
        )

    @property
    def aws(self) -> AWSConfig:
        return AWSConfig(
            region=self.aws_region, access_key_id=self.aws_access_key,
            secret_access_key=self.aws_secret_key, endpoint_url=self.aws_endpoint
        )

    @property
    def s3(self) -> S3Config:
        return S3Config(bucket_name=self.s3_bucket)

    @property
    def sqs(self) -> SQSConfig:
        return SQSConfig(task_queue_url=self.sqs_url)

    @property
    def db(self) -> DBConfig:
        return DBConfig(uri=self.db_uri)

    @property
    def auth(self) -> AuthConfig:
        return AuthConfig(jwt_secret=self.jwt_secret, user_id_header=self.user_id_header)

config = AppConfig()
