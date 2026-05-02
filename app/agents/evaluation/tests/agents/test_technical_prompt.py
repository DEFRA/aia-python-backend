"""Tests for the technical assessment prompt template (loaded from .md files)."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "agents" / "prompts"


def _load(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


def test_technical_prompt_renders_xml_questions_and_url() -> None:
    """Formatting the user template should produce the expected XML question
    and category URL shape, mirroring the security prompt contract.
    """
    template = _load("technical_user.md")
    questions_block: str = (
        '1. <question reference="T1.a">Is a Record of Processing Activity (Article 30) '
        "maintained?</question>\n"
        '2. <question reference="T2.b">Are retention schedules documented for personal data?'
        "</question>"
    )
    policy_doc_url: str = "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/"
    document: str = "Sample policy text."

    rendered: str = template.format(
        document=document,
        category_url=policy_doc_url,
        questions=questions_block,
    )

    assert "<document>" in rendered
    assert "Sample policy text." in rendered
    assert f"<category_url>{policy_doc_url}</category_url>" in rendered
    assert '<question reference="T1.a">' in rendered
    assert '<question reference="T2.b">' in rendered
    assert '"Technical"' in rendered


def test_technical_system_prompt_specifies_technical_top_level_key() -> None:
    """The system prompt must instruct the model to use ``Technical`` as the
    top-level JSON key, otherwise the agent's parser will fail to extract.
    """
    system_prompt = _load("technical_system.md")
    assert '"Technical"' in system_prompt


def test_technical_system_prompt_covers_required_uk_ig_topics() -> None:
    """The system prompt should explicitly name the key UK information-governance
    domains the agent is expected to evaluate (DPA 2018 / UK GDPR / records mgmt).
    """
    system_prompt = _load("technical_system.md")
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
        assert term in system_prompt, f"Expected '{term}' in technical system prompt"
