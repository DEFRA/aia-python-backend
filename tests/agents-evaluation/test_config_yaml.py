"""Tests covering the YAML-backed config sources for the evaluation pipeline."""

from __future__ import annotations

import pytest

from src.config import PipelineConfig, TechnicalAgentConfig


def test_yaml_loads_technical_agent_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """``TechnicalAgentConfig`` instantiated with no overrides should pick up
    the defaults defined under ``agents.technical`` in ``config.yaml``.
    """
    for key in ("TECHNICAL_MODEL", "TECHNICAL_MAX_TOKENS", "TECHNICAL_TEMPERATURE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    config: TechnicalAgentConfig = TechnicalAgentConfig()

    assert config.model
    assert config.max_tokens > 0
    assert 0.0 <= config.temperature <= 1.0


def test_yaml_pipeline_agent_tag_map_lists_technical(monkeypatch: pytest.MonkeyPatch) -> None:
    """``pipeline.agent_tag_map`` in ``config.yaml`` must list ``technical``
    with the eleven UK information-governance tags.
    """
    monkeypatch.delenv("PIPELINE_AGENT_TAG_MAP", raising=False)

    config: PipelineConfig = PipelineConfig()

    assert "technical" in config.agent_tag_map
    expected: set[str] = {
        "data_protection",
        "records_of_processing",
        "data_retention",
        "data_subject_rights",
        "lawful_basis",
        "privacy_notice",
        "dpia",
        "data_sharing",
        "ig_governance",
        "audit_trail",
        "information_classification",
    }
    assert set(config.agent_tag_map["technical"]) == expected


def test_yaml_pipeline_agent_types_lists_two_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """``pipeline.agent_types`` must list exactly the two surviving agents."""
    monkeypatch.delenv("PIPELINE_AGENT_TYPES", raising=False)

    config: PipelineConfig = PipelineConfig()

    assert config.agent_types == ["security", "technical"]


def test_yaml_pipeline_agent_tag_map_drops_legacy_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The four removed agents must not appear in ``agent_tag_map``."""
    monkeypatch.delenv("PIPELINE_AGENT_TAG_MAP", raising=False)

    config: PipelineConfig = PipelineConfig()

    for legacy in ("data", "risk", "ea", "solution", "governance"):
        assert legacy not in config.agent_tag_map
