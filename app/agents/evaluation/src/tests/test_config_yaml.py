"""Tests for the centralised config.yaml file and YAML settings source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

CONFIG_YAML_PATH: Path = Path(__file__).resolve().parent.parent.parent / "config.yaml"


def test_config_yaml_file_exists() -> None:
    """config.yaml must exist at app/agents/evaluation/config.yaml."""
    assert CONFIG_YAML_PATH.is_file(), f"Expected config.yaml at {CONFIG_YAML_PATH}"


def test_config_yaml_top_level_keys() -> None:
    """config.yaml must define all required top-level sections."""
    data: dict[str, Any] = yaml.safe_load(CONFIG_YAML_PATH.read_text(encoding="utf-8"))
    expected_keys: set[str] = {
        "agents",
        "cache",
        "cloudwatch",
        "eventbridge",
        "pipeline",
        "parser",
        "redis",
        "database",
    }
    assert expected_keys.issubset(data.keys())


def test_config_yaml_agents_section() -> None:
    """The agents section must define all 7 agent configs."""
    data: dict[str, Any] = yaml.safe_load(CONFIG_YAML_PATH.read_text(encoding="utf-8"))
    agents: dict[str, Any] = data["agents"]
    expected: set[str] = {"security", "gdpr", "data", "risk", "ea", "solution", "tagging"}
    assert expected.issubset(agents.keys())


def test_config_yaml_cache_ttls() -> None:
    """The cache section must define all 9 TTL values as positive ints."""
    data: dict[str, Any] = yaml.safe_load(CONFIG_YAML_PATH.read_text(encoding="utf-8"))
    cache: dict[str, Any] = data["cache"]
    expected: set[str] = {
        "ttl_chunks",
        "ttl_tagged",
        "ttl_sections",
        "ttl_questions",
        "ttl_result",
        "ttl_results_count",
        "ttl_compiled",
        "ttl_stage8_count",
        "ttl_receipt",
    }
    assert expected.issubset(cache.keys())
    for key in expected:
        assert isinstance(cache[key], int)
        assert cache[key] > 0


def test_config_yaml_cloudwatch_namespace() -> None:
    """The cloudwatch section must define a namespace."""
    data: dict[str, Any] = yaml.safe_load(CONFIG_YAML_PATH.read_text(encoding="utf-8"))
    assert data["cloudwatch"]["namespace"] == "Defra/Pipeline"


def test_config_yaml_pipeline_agent_types() -> None:
    """The pipeline section must list the 5 agent types."""
    data: dict[str, Any] = yaml.safe_load(CONFIG_YAML_PATH.read_text(encoding="utf-8"))
    assert data["pipeline"]["agent_types"] == ["security", "data", "risk", "ea", "solution"]


def test_config_yaml_agent_tag_map_complete() -> None:
    """agent_tag_map must contain an entry for each of the 5 agent types."""
    data: dict[str, Any] = yaml.safe_load(CONFIG_YAML_PATH.read_text(encoding="utf-8"))
    tag_map: dict[str, list[str]] = data["pipeline"]["agent_tag_map"]
    for agent in ("security", "data", "risk", "ea", "solution"):
        assert agent in tag_map
        assert isinstance(tag_map[agent], list)
        assert len(tag_map[agent]) > 0


def test_config_yaml_parser_defaults() -> None:
    """parser section must define min_text_chars and chunk_max_chars."""
    data: dict[str, Any] = yaml.safe_load(CONFIG_YAML_PATH.read_text(encoding="utf-8"))
    parser: dict[str, Any] = data["parser"]
    assert isinstance(parser["min_text_chars"], int)
    assert isinstance(parser["chunk_max_chars"], int)


def test_config_yaml_no_secrets() -> None:
    """config.yaml must not contain secret fields."""
    raw: str = CONFIG_YAML_PATH.read_text(encoding="utf-8")
    forbidden: list[str] = [
        "ANTHROPIC_API_KEY",
        "api_key:",
        "db_password",
        "DB_PASSWORD",
        "db_user",
        "db_host",
        "db_name",
        "REDIS_HOST",
    ]
    for token in forbidden:
        assert token not in raw, f"config.yaml must not contain '{token}'"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
