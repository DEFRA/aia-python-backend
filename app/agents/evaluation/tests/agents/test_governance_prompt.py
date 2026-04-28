"""Tests for the governance assessment prompt template."""

from __future__ import annotations

from src.agents.prompts.governance import (
    GOVERNANCE_ASSESSMENT_SYSTEM_PROMPT,
    GOVERNANCE_ASSESSMENT_USER_TEMPLATE,
)


def test_governance_prompt_renders_xml_questions_and_url() -> None:
    """Formatting the user template should produce the expected XML question
    and category URL shape, mirroring the security prompt contract.
    """
    questions_block: str = (
        '1. <question reference="G1.a">Is a Record of Processing Activity (Article 30) '
        "maintained?</question>\n"
        '2. <question reference="G2.b">Are retention schedules documented for personal data?'
        "</question>"
    )
    category_url: str = "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/"
    document: str = "Sample policy text."

    rendered: str = GOVERNANCE_ASSESSMENT_USER_TEMPLATE.format(
        document=document,
        category_url=category_url,
        questions=questions_block,
    )

    assert "<document>" in rendered
    assert "Sample policy text." in rendered
    assert f"<category_url>{category_url}</category_url>" in rendered
    assert '<question reference="G1.a">' in rendered
    assert '<question reference="G2.b">' in rendered
    assert '"Governance"' in rendered


def test_governance_system_prompt_specifies_governance_top_level_key() -> None:
    """The system prompt must instruct the model to use ``Governance`` as the
    top-level JSON key, otherwise the agent's parser will fail to extract.
    """
    assert '"Governance"' in GOVERNANCE_ASSESSMENT_SYSTEM_PROMPT


def test_governance_system_prompt_covers_required_uk_ig_topics() -> None:
    """The system prompt should explicitly name the key UK information-governance
    domains the agent is expected to evaluate (DPA 2018 / UK GDPR / records mgmt).
    """
    required_terms: list[str] = [
        "UK GDPR",
        "DPA 2018",
        "Article 6",
        "Article 30",
        "DPIA",
        "DPO",
        "IAO",
        "SIRO",
        "OFFICIAL",
    ]
    for term in required_terms:
        assert term in GOVERNANCE_ASSESSMENT_SYSTEM_PROMPT, f"Expected '{term}' in system prompt"
