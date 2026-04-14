"""Configuration classes for AI agents and database, loaded from environment variables."""

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings


class SecurityAgentConfig(BaseSettings):
    """Configuration for the SecurityAgent.

    Values are read from environment variables (or a .env file).
    See .env.example for the full list of required variables.
    """

    model: str = Field(default="claude-opus-4-6")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.0)
    api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = {"populate_by_name": True}


class DatabaseConfig(BaseSettings):
    """Connection settings for the PostgreSQL questions database.

    Values are read from environment variables (or a .env file).
    See .env.example for the full list of required variables.
    """

    db_host: str = Field(default="localhost", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_name: str = Field(alias="DB_NAME")
    db_user: str = Field(alias="DB_USER")
    db_password: str = Field(alias="DB_PASSWORD")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dsn(self) -> str:
        """Build an asyncpg-compatible connection string from individual fields."""
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    model_config = {"populate_by_name": True}


class GDPRAgentConfig(BaseSettings):
    """Configuration for the GDPRComplianceAgent.

    Values are read from environment variables (or a .env file).
    See .env.example for the full list of required variables.
    """

    model: str = Field(default="claude-opus-4-6")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.0)
    api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = {"populate_by_name": True}


class DataAgentConfig(BaseSettings):
    """Configuration for the DataAgent.

    Values are read from environment variables (or a .env file).
    See .env.example for the full list of required variables.
    """

    model: str = Field(default="claude-sonnet-4-6")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.0)
    api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = {"populate_by_name": True}


class RiskAgentConfig(BaseSettings):
    """Configuration for the RiskAgent.

    Values are read from environment variables (or a .env file).
    See .env.example for the full list of required variables.
    """

    model: str = Field(default="claude-sonnet-4-6")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.0)
    api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = {"populate_by_name": True}


class EAAgentConfig(BaseSettings):
    """Configuration for the EAAgent.

    Values are read from environment variables (or a .env file).
    See .env.example for the full list of required variables.
    """

    model: str = Field(default="claude-sonnet-4-6")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.0)
    api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = {"populate_by_name": True}


class SolutionAgentConfig(BaseSettings):
    """Configuration for the SolutionAgent.

    Values are read from environment variables (or a .env file).
    See .env.example for the full list of required variables.
    """

    model: str = Field(default="claude-sonnet-4-6")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.0)
    api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = {"populate_by_name": True}


class RedisConfig(BaseSettings):
    """Connection settings for the ElastiCache Redis cluster.

    Values are read from environment variables (or a .env file).
    ``ssl`` defaults to ``True`` for ElastiCache in-transit encryption.
    """

    host: str = Field(alias="REDIS_HOST")
    port: int = Field(default=6379, alias="REDIS_PORT")
    ssl: bool = Field(default=True, alias="REDIS_SSL")
    db: int = Field(default=0, alias="REDIS_DB")
    socket_timeout: float = Field(default=5.0)
    socket_connect_timeout: float = Field(default=3.0)

    model_config = {"populate_by_name": True}


class EventBridgeConfig(BaseSettings):
    """Configuration for the EventBridge custom event bus.

    Values are read from environment variables (or a .env file).
    ``source`` is a fixed literal — not configurable via env.
    """

    bus_name: str = Field(default="defra-pipeline", alias="EVENTBRIDGE_BUS_NAME")
    source: str = Field(default="defra.pipeline")
    region: str = Field(default="eu-west-2", alias="AWS_REGION")

    model_config = {"populate_by_name": True}
