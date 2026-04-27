import pytest

from app.orchestrator.summary import MarkdownSummaryGenerator, SummaryGenerator

DOC_ID = "aaaaaaaa-0000-0000-0000-000000000001"
TASK_PREFIX = f"{DOC_ID}_"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_markdown_summary_generator_satisfies_protocol():
    gen = MarkdownSummaryGenerator()
    assert isinstance(gen, SummaryGenerator)


# ---------------------------------------------------------------------------
# Empty / degenerate inputs
# ---------------------------------------------------------------------------


def test_generate_empty_dict_returns_empty_string():
    gen = MarkdownSummaryGenerator()
    assert gen.generate({}) == ""


def test_generate_preserves_empty_string_for_no_results():
    gen = MarkdownSummaryGenerator()
    result = gen.generate({})
    assert result == ""


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------


def test_generate_includes_top_level_heading():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}security": {"score": 85}})
    assert "# AI Assessment Report" in output


def test_generate_single_agent_creates_section_heading():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}security": {"score": 85}})
    assert "## Security" in output


def test_generate_agent_name_is_title_cased():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}data": {"score": 70}})
    assert "## Data" in output


def test_generate_hyphenated_agent_type_becomes_title_case():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}data-governance": {"score": 70}})
    assert "## Data Governance" in output


# ---------------------------------------------------------------------------
# Result formatting — flat dict
# ---------------------------------------------------------------------------


def test_generate_flat_dict_includes_field_headings():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}security": {"score": 85, "status": "pass"}})
    assert "**Score:**" in output
    assert "**Status:**" in output


def test_generate_flat_dict_includes_values():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}security": {"score": 85}})
    assert "85" in output


def test_generate_underscore_key_becomes_title_case_heading():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}security": {"risk_level": "low"}})
    assert "**Risk Level:**" in output


# ---------------------------------------------------------------------------
# Result formatting — list values
# ---------------------------------------------------------------------------


def test_generate_list_value_renders_as_bullet_points():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({
        f"{TASK_PREFIX}security": {"findings": ["no auth bypass", "TLS enabled"]}
    })
    assert "- no auth bypass" in output
    assert "- TLS enabled" in output


def test_generate_list_value_includes_heading():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}security": {"findings": ["item"]}})
    assert "**Findings:**" in output


# ---------------------------------------------------------------------------
# Result formatting — nested dict
# ---------------------------------------------------------------------------


def test_generate_nested_dict_renders_sub_items():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({
        f"{TASK_PREFIX}security": {"breakdown": {"auth": "pass", "tls": "pass"}}
    })
    assert "auth: pass" in output
    assert "tls: pass" in output


def test_generate_nested_dict_includes_parent_heading():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}security": {"breakdown": {"k": "v"}}})
    assert "**Breakdown:**" in output


# ---------------------------------------------------------------------------
# Result formatting — non-dict result
# ---------------------------------------------------------------------------


def test_generate_non_dict_result_is_stringified():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}security": "Agent response as plain text"})
    assert "Agent response as plain text" in output


def test_generate_integer_result_is_stringified():
    gen = MarkdownSummaryGenerator()
    output = gen.generate({f"{TASK_PREFIX}security": 42})
    assert "42" in output


# ---------------------------------------------------------------------------
# Multiple agents
# ---------------------------------------------------------------------------


def test_generate_multiple_agents_all_have_sections():
    gen = MarkdownSummaryGenerator()
    results = {
        f"{TASK_PREFIX}security": {"score": 80},
        f"{TASK_PREFIX}data": {"score": 75},
        f"{TASK_PREFIX}risk": {"score": 90},
    }
    output = gen.generate(results)
    assert "## Security" in output
    assert "## Data" in output
    assert "## Risk" in output


def test_generate_multiple_agents_sorted_alphabetically():
    gen = MarkdownSummaryGenerator()
    results = {
        f"{TASK_PREFIX}security": {"score": 80},
        f"{TASK_PREFIX}data": {"score": 75},
    }
    output = gen.generate(results)
    data_pos = output.index("## Data")
    security_pos = output.index("## Security")
    assert data_pos < security_pos


# ---------------------------------------------------------------------------
# _agent_name helper (via generate output)
# ---------------------------------------------------------------------------


def test_agent_name_uses_last_underscore_segment_only():
    gen = MarkdownSummaryGenerator()
    # task_id = "{long-uuid-with-dashes}_security"
    complex_task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_security"
    output = gen.generate({complex_task_id: {"score": 1}})
    assert "## Security" in output
    assert "Aaaaaaaa" not in output
