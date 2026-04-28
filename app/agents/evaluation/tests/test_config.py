"""Tests for evaluation pipeline configuration models."""

import pytest

from src.config import EventBridgeConfig, GovernanceAgentConfig, PipelineConfig


def test_redis_config_is_gone() -> None:
    """Plan 11: RedisConfig must no longer be importable."""
    with pytest.raises(ImportError):
        from src.config import RedisConfig  # noqa: F401


def test_cache_config_is_gone() -> None:
    """Plan 11: CacheConfig and its TTLs were removed alongside Redis."""
    with pytest.raises(ImportError):
        from src.config import CacheConfig  # noqa: F401


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
    monkeypatch.setenv("GOVERNANCE_MODEL", "test-model")
    monkeypatch.setenv("GOVERNANCE_MAX_TOKENS", "2048")
    monkeypatch.setenv("GOVERNANCE_TEMPERATURE", "0.5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    config: GovernanceAgentConfig = GovernanceAgentConfig()

    assert config.model == "test-model"
    assert config.max_tokens == 2048
    assert config.temperature == 0.5
    assert config.api_key == "test-key"
