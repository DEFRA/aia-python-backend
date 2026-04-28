"""Tests for the tagging prompt taxonomy."""

from __future__ import annotations

from src.agents.prompts.tagging import SYSTEM_PROMPT, TAXONOMY


def test_tagging_prompt_lists_governance_tags() -> None:
    """The eleven UK information-governance tags must appear verbatim in the
    tagging system prompt's taxonomy section.
    """
    governance_tags: list[str] = [
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
    ]
    for tag in governance_tags:
        assert tag in SYSTEM_PROMPT, f"Expected '{tag}' in tagging system prompt"
        assert tag in TAXONOMY, f"Expected '{tag}' in TAXONOMY map"


def test_tagging_taxonomy_drops_legacy_only_tags() -> None:
    """Tags exclusively used by the four removed agents (and not retained for
    security or governance) must be removed from the taxonomy.
    """
    surviving_tags: set[str] = {
        # security set
        "authentication",
        "authorisation",
        "encryption",
        "vulnerability_management",
        "secrets_management",
        "network_security",
        # governance set
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
    legacy_only: set[str] = {
        "audit_logging",
        "data_governance",
        "incident_response",
        "compliance",
    }
    for tag in legacy_only:
        assert tag not in surviving_tags
        assert tag not in TAXONOMY, f"Legacy-only tag '{tag}' should be dropped from taxonomy"
