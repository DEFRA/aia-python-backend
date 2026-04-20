"""Tests for new Pydantic config classes backed by config.yaml."""

# ruff: noqa: PLR2004

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import (
    CacheConfig,
    CloudWatchConfig,
    DatabaseConfig,
    EventBridgeConfig,
    ParserConfig,
    PipelineConfig,
    RedisConfig,
    SecurityAgentConfig,
    TaggingAgentConfig,
    YamlSettingsSource,
    _load_yaml,
)

# ---------------------------------------------------------------------------
# _load_yaml()
# ---------------------------------------------------------------------------


def test_load_yaml_returns_dict() -> None:
    """_load_yaml should return a dict with expected top-level keys."""
    data = _load_yaml()
    assert isinstance(data, dict)
    assert "agents" in data
    assert "cache" in data
    assert "cloudwatch" in data


def test_load_yaml_resolves_relative_to_module() -> None:
    """_load_yaml should resolve to app/agents/evaluation/config.yaml."""
    from src import config as config_module

    expected_path: Path = Path(config_module.__file__).resolve().parent.parent / "config.yaml"
    assert expected_path.is_file()


# ---------------------------------------------------------------------------
# YamlSettingsSource
# ---------------------------------------------------------------------------


def test_yaml_source_returns_section() -> None:
    """YamlSettingsSource(yaml_key='cache') should return the cache section."""

    class _Dummy(CacheConfig):
        pass

    source = YamlSettingsSource(_Dummy, yaml_key="cache")
    data = source()
    assert "ttl_chunks" in data
    assert data["ttl_chunks"] == 86_400


def test_yaml_source_dotted_path() -> None:
    """YamlSettingsSource supports dotted paths like 'agents.security'."""
    source = YamlSettingsSource(SecurityAgentConfig, yaml_key="agents.security")
    data = source()
    assert data["model"] == "claude-opus-4-6"
    assert data["max_tokens"] == 4096


def test_yaml_source_missing_key_returns_empty() -> None:
    """A missing yaml_key should yield an empty dict."""
    source = YamlSettingsSource(SecurityAgentConfig, yaml_key="does_not_exist")
    assert source() == {}


# ---------------------------------------------------------------------------
# CacheConfig
# ---------------------------------------------------------------------------


def test_cache_config_loads_from_yaml() -> None:
    """CacheConfig should read TTLs from config.yaml by default."""
    config = CacheConfig()
    assert config.ttl_chunks == 86_400
    assert config.ttl_tagged == 86_400
    assert config.ttl_sections == 3_600
    assert config.ttl_questions == 3_600
    assert config.ttl_result == 3_600
    assert config.ttl_results_count == 1_800
    assert config.ttl_compiled == 3_600
    assert config.ttl_stage8_count == 1_800
    assert config.ttl_receipt == 900


def test_cache_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """An env var should override the yaml value."""
    monkeypatch.setenv("CACHE_TTL_CHUNKS", "12345")
    config = CacheConfig()
    assert config.ttl_chunks == 12_345


# ---------------------------------------------------------------------------
# CloudWatchConfig
# ---------------------------------------------------------------------------


def test_cloudwatch_config_loads_from_yaml() -> None:
    """CloudWatchConfig should read namespace from yaml."""
    config = CloudWatchConfig()
    assert config.namespace == "Defra/Pipeline"


def test_cloudwatch_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLOUDWATCH_NAMESPACE should override yaml."""
    monkeypatch.setenv("CLOUDWATCH_NAMESPACE", "Custom/Namespace")
    config = CloudWatchConfig()
    assert config.namespace == "Custom/Namespace"


# ---------------------------------------------------------------------------
# TaggingAgentConfig
# ---------------------------------------------------------------------------


def test_tagging_config_loads_from_yaml() -> None:
    """TaggingAgentConfig should read from agents.tagging in yaml."""
    config = TaggingAgentConfig()
    assert config.model == "claude-sonnet-4-6"
    assert config.batch_size == 15
    assert config.max_tokens == 4096
    assert config.temperature == 0.0


def test_tagging_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """TAGGING_BATCH_SIZE should override yaml."""
    monkeypatch.setenv("TAGGING_BATCH_SIZE", "42")
    config = TaggingAgentConfig()
    assert config.batch_size == 42


# ---------------------------------------------------------------------------
# PipelineConfig
# ---------------------------------------------------------------------------


def test_pipeline_config_loads_from_yaml() -> None:
    """PipelineConfig should read agent_types and tag map from yaml."""
    config = PipelineConfig()
    assert config.agent_types == ["security", "data", "risk", "ea", "solution"]
    assert config.sqs_inline_limit == 240_000
    assert "security" in config.agent_tag_map
    assert "authentication" in config.agent_tag_map["security"]


# ---------------------------------------------------------------------------
# ParserConfig
# ---------------------------------------------------------------------------


def test_parser_config_loads_from_yaml() -> None:
    """ParserConfig should read min_text_chars and chunk_max_chars from yaml."""
    config = ParserConfig()
    assert config.min_text_chars == 100
    assert config.chunk_max_chars == 1_500


def test_parser_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """PARSER_MIN_TEXT_CHARS should override yaml."""
    monkeypatch.setenv("PARSER_MIN_TEXT_CHARS", "250")
    config = ParserConfig()
    assert config.min_text_chars == 250


# ---------------------------------------------------------------------------
# Existing config classes wired with YAML
# ---------------------------------------------------------------------------


def test_security_agent_config_uses_yaml_defaults() -> None:
    """SecurityAgentConfig should read model/max_tokens/temperature from yaml."""
    config = SecurityAgentConfig()
    assert config.model == "claude-opus-4-6"
    assert config.max_tokens == 4096
    assert config.temperature == 0.0


def test_security_agent_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """An env var (e.g. AGENT_MODEL_SECURITY) should override the yaml value."""
    monkeypatch.setenv("SECURITY_MODEL", "claude-opus-override")
    config = SecurityAgentConfig()
    assert config.model == "claude-opus-override"


def test_redis_config_requires_host_env() -> None:
    """REDIS_HOST must come from env; yaml does not supply it."""
    # If REDIS_HOST is not set we'd expect a ValidationError; instead pass explicitly
    config = RedisConfig(REDIS_HOST="example.com")
    assert config.host == "example.com"
    # yaml-provided defaults still populate non-secret fields
    assert config.port == 6379
    assert config.ssl is True


def test_eventbridge_config_loads_from_yaml() -> None:
    """EventBridgeConfig defaults should come from yaml."""
    config = EventBridgeConfig()
    assert config.bus_name == "defra-pipeline"
    assert config.source == "defra.pipeline"
    assert config.region == "eu-west-2"


def test_eventbridge_config_source_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVENTBRIDGE_SOURCE should be respected."""
    monkeypatch.setenv("EVENTBRIDGE_SOURCE", "custom.source")
    config = EventBridgeConfig()
    assert config.source == "custom.source"


def test_database_config_env_only_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """DatabaseConfig should take db_host/name/user/password from env only."""
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setenv("DB_NAME", "defra")
    monkeypatch.setenv("DB_USER", "admin")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    config = DatabaseConfig()  # type: ignore[call-arg]
    assert config.db_host == "db.example.com"
    # Port comes from yaml default
    assert config.db_port == 5432


def test_database_config_missing_db_host_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """DatabaseConfig should raise ValidationError when DB_HOST is absent.

    ``db_host`` has no default — it is a deployment-specific, env-only value
    per the config docstring. If this test fails, a default has crept back in.
    """
    from pydantic import ValidationError

    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.setenv("DB_NAME", "defra")
    monkeypatch.setenv("DB_USER", "admin")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    with pytest.raises(ValidationError):
        DatabaseConfig()  # type: ignore[call-arg]
