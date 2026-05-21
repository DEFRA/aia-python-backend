"""Tests for evaluation pipeline configuration models."""

import pytest
from pydantic import ValidationError

from app.agents.evaluation.src.config import (
    EventBridgeConfig,
    LLMConfig,
    PipelineConfig,
    RetryConfig,
    TechnicalAgentConfig,
)


def test_redis_config_is_gone() -> None:
    """Plan 11: RedisConfig must no longer be importable."""
    with pytest.raises(ImportError):
        from app.agents.evaluation.src.config import RedisConfig  # noqa: F401


def test_cache_config_is_gone() -> None:
    """Plan 11: CacheConfig and its TTLs were removed alongside Redis."""
    with pytest.raises(ImportError):
        from app.agents.evaluation.src.config import CacheConfig  # noqa: F401


def test_gdpr_agent_config_is_gone() -> None:
    """GDPRAgentConfig was removed in Phase 3."""
    with pytest.raises(ImportError):
        from app.agents.evaluation.src.config import GDPRAgentConfig  # noqa: F401


def test_governance_agent_config_is_gone() -> None:
    """GovernanceAgentConfig was renamed to TechnicalAgentConfig in Phase 3."""
    with pytest.raises(ImportError):
        from app.agents.evaluation.src.config import GovernanceAgentConfig  # noqa: F401


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
    """PipelineConfig should default ``agent_types`` to the two surviving agents."""
    config: PipelineConfig = PipelineConfig.model_construct()
    assert config.agent_types == ["security", "technical"]


def test_llm_config_loads_sdk_max_retries_and_timeout_from_yaml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMConfig reads sdk_max_retries and request_timeout_s from config.yaml."""
    for key in ("LLM_SDK_MAX_RETRIES", "LLM_REQUEST_TIMEOUT_S"):
        monkeypatch.delenv(key, raising=False)

    config: LLMConfig = LLMConfig()

    assert config.sdk_max_retries == 0
    assert config.request_timeout_s == 120.0


def test_llm_config_env_var_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_SDK_MAX_RETRIES env var should override the yaml value."""
    monkeypatch.setenv("LLM_SDK_MAX_RETRIES", "5")
    config: LLMConfig = LLMConfig()
    assert config.sdk_max_retries == 5


def test_retry_config_defaults_match_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """RetryConfig should pick up the four documented yaml values."""
    for key in (
        "RETRY_MAX_ATTEMPTS",
        "RETRY_INITIAL_WAIT_S",
        "RETRY_MAX_WAIT_S",
        "RETRY_JITTER_S",
    ):
        monkeypatch.delenv(key, raising=False)

    config: RetryConfig = RetryConfig()

    assert config.max_attempts == 3
    assert config.initial_wait_s == 2.0
    assert config.max_wait_s == 30.0
    assert config.jitter_s == 1.0


def test_retry_config_validators_reject_bad_values() -> None:
    """RetryConfig should reject max_attempts < 1 and max_wait_s < initial_wait_s."""
    with pytest.raises(ValidationError):
        RetryConfig(max_attempts=0)

    with pytest.raises(ValidationError):
        RetryConfig(initial_wait_s=10.0, max_wait_s=5.0)


def test_technical_agent_config_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """TechnicalAgentConfig should read TECHNICAL_* env vars via Pydantic aliases."""
    monkeypatch.setenv("TECHNICAL_MODEL", "test-model")
    monkeypatch.setenv("TECHNICAL_MAX_TOKENS", "2048")
    monkeypatch.setenv("TECHNICAL_TEMPERATURE", "0.5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    config: TechnicalAgentConfig = TechnicalAgentConfig()

    assert config.model == "test-model"
    assert config.max_tokens == 2048
    assert config.temperature == 0.5
    assert config.api_key == "test-key"
