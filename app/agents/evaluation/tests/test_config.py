"""Tests for RedisConfig and EventBridgeConfig."""

import pytest

from src.config import EventBridgeConfig, GovernanceAgentConfig, PipelineConfig, RedisConfig


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


def test_pipeline_config_default_agent_types() -> None:
    """PipelineConfig should default ``agent_types`` to the two surviving agents.

    The yaml file overrides this default with the same list, but the field
    default itself is the contract: any caller building a ``PipelineConfig``
    in isolation (e.g. inside a test) must see the new two-agent set.
    """
    config: PipelineConfig = PipelineConfig.model_construct()
    assert config.agent_types == ["security", "governance"]


def test_governance_agent_config_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """GovernanceAgentConfig should read GOVERNANCE_* env vars via Pydantic aliases."""
    monkeypatch.setenv("GOVERNANCE_MODEL", "claude-test-model")
    monkeypatch.setenv("GOVERNANCE_MAX_TOKENS", "2048")
    monkeypatch.setenv("GOVERNANCE_TEMPERATURE", "0.5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    config: GovernanceAgentConfig = GovernanceAgentConfig()

    assert config.model == "claude-test-model"
    assert config.max_tokens == 2048
    assert config.temperature == 0.5
    assert config.api_key == "test-key"
