"""Tests covering the YAML-backed config sources for the evaluation pipeline."""

from __future__ import annotations

import pytest

from src.config import GovernanceAgentConfig, PipelineConfig


def test_yaml_loads_governance_agent_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GovernanceAgentConfig`` instantiated with no overrides should pick up
    the defaults defined under ``agents.governance`` in ``config.yaml``.
    """
    # Strip env overrides so the YAML source wins.
    for key in ("GOVERNANCE_MODEL", "GOVERNANCE_MAX_TOKENS", "GOVERNANCE_TEMPERATURE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    config: GovernanceAgentConfig = GovernanceAgentConfig()

    assert config.model
    assert config.max_tokens > 0
    assert 0.0 <= config.temperature <= 1.0


def test_yaml_pipeline_agent_tag_map_lists_governance(monkeypatch: pytest.MonkeyPatch) -> None:
    """``pipeline.agent_tag_map`` in ``config.yaml`` must list ``governance``
    with the eleven UK information-governance tags.
    """
    monkeypatch.delenv("PIPELINE_AGENT_TAG_MAP", raising=False)

    config: PipelineConfig = PipelineConfig()

    assert "governance" in config.agent_tag_map
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
    assert set(config.agent_tag_map["governance"]) == expected


def test_yaml_pipeline_agent_types_lists_two_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """``pipeline.agent_types`` must list exactly the two surviving agents."""
    monkeypatch.delenv("PIPELINE_AGENT_TYPES", raising=False)

    config: PipelineConfig = PipelineConfig()

    assert config.agent_types == ["security", "governance"]


def test_yaml_pipeline_agent_tag_map_drops_legacy_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The four removed agents must not appear in ``agent_tag_map``."""
    monkeypatch.delenv("PIPELINE_AGENT_TAG_MAP", raising=False)

    config: PipelineConfig = PipelineConfig()

    for legacy in ("data", "risk", "ea", "solution"):
        assert legacy not in config.agent_tag_map
