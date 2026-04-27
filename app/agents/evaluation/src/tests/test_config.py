"""Tests for RedisConfig and EventBridgeConfig."""

from src.config import EventBridgeConfig, RedisConfig


def test_redis_config_defaults() -> None:
    """RedisConfig should populate with sensible defaults."""
    config = RedisConfig(REDIS_HOST="localhost")
    assert config.host == "localhost"
    assert config.port == 6379
    assert config.ssl is True
    assert config.db == 0
    assert config.socket_timeout == 5.0
    assert config.socket_connect_timeout == 3.0


def test_redis_config_custom_values() -> None:
    """RedisConfig should accept overrides via kwargs."""
    config = RedisConfig(
        host="redis.example.com",
        port=6380,
        ssl=False,
        db=2,
    )
    assert config.host == "redis.example.com"
    assert config.port == 6380
    assert config.ssl is False
    assert config.db == 2


def test_eventbridge_config_defaults() -> None:
    """EventBridgeConfig should have correct defaults."""
    config = EventBridgeConfig()
    assert config.bus_name == "defra-pipeline"
    assert config.source == "defra.pipeline"
    assert config.region == "eu-west-2"


def test_eventbridge_config_custom_values() -> None:
    """EventBridgeConfig should accept overrides."""
    config = EventBridgeConfig(
        bus_name="custom-bus",
        region="us-east-1",
    )
    assert config.bus_name == "custom-bus"
    assert config.region == "us-east-1"
