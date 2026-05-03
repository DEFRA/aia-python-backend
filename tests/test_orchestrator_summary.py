import sys
from pathlib import Path


_EVAL_ROOT = Path(__file__).resolve().parent.parent / "app" / "agents" / "evaluation"
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from src.agents.schemas import AgentResult, AssessmentRow, Summary  # noqa: E402

from app.orchestrator.summary import MarkdownReportGenerator, SummaryGenerator  # noqa: E402

DOC_ID = "aaaaaaaa-0000-0000-0000-000000000001"

SECTION_LABELS = {"security": "Security", "data": "Data", "risk": "Risk"}
AGENT_ORDER = ["security", "data", "risk"]


def _make_result(
    ratings: list[str] | None = None,
    filename: str = "policy.pdf",
    url: str = "https://example.com/policy.pdf",
) -> AgentResult:
    if ratings is None:
        ratings = ["Green"]
    assessments = [
        AssessmentRow(
            Question=f"Question {i}",
            Rating=r,
            Comments=f"Comment {i}",
            Reference=f"C{i}.a",
        )
        for i, r in enumerate(ratings, 1)
    ]
    return AgentResult(
        policy_doc_filename=filename,
        policy_doc_url=url,
        assessments=assessments,
        summary=Summary(
            Interpretation="Satisfactory",
            Overall_Comments="No critical gaps.",
        ),
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_markdown_report_generator_satisfies_protocol():
    gen = MarkdownReportGenerator()
    assert isinstance(gen, SummaryGenerator)


# ---------------------------------------------------------------------------
# Empty / degenerate inputs
# ---------------------------------------------------------------------------


def test_generate_empty_results_produces_output():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={},
        document_title="Test Document",
        section_labels=SECTION_LABELS,
        agent_type_order=AGENT_ORDER,
    )
    assert "# Test Document" in output


def test_generate_none_results_are_skipped():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": None},
        document_title="Test Document",
        section_labels=SECTION_LABELS,
        agent_type_order=AGENT_ORDER,
    )
    assert "## Security" not in output


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------


def test_generate_includes_document_title():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result()},
        document_title="My Policy Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "# My Policy Doc" in output


def test_generate_single_agent_creates_section_heading():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result()},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "## Security" in output


def test_generate_uses_section_labels_for_heading():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"data": _make_result()},
        document_title="Doc",
        section_labels={"data": "Data Governance"},
        agent_type_order=["data"],
    )
    assert "## Data Governance" in output


def test_generate_falls_back_to_title_case_when_no_label():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"risk": _make_result()},
        document_title="Doc",
        section_labels={},
        agent_type_order=["risk"],
    )
    assert "## Risk" in output


def test_generate_includes_policy_doc_filename():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result(filename="security_policy.pdf")},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "security_policy.pdf" in output


def test_generate_includes_policy_doc_url():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result(url="https://example.com/sec.pdf")},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "https://example.com/sec.pdf" in output


# ---------------------------------------------------------------------------
# Assessment table
# ---------------------------------------------------------------------------


def test_generate_includes_assessment_table_headers():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result()},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "| Question |" in output
    assert "| Reference |" in output
    assert "| Rating |" in output
    assert "| Comments |" in output


def test_generate_includes_rating_emoji_green():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result(ratings=["Green"])},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "🟢" in output


def test_generate_includes_rating_emoji_red():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result(ratings=["Red"])},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "🔴" in output


def test_generate_includes_rating_emoji_amber():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result(ratings=["Amber"])},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "🟡" in output


def test_generate_includes_summary_interpretation():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result()},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "Satisfactory" in output


# ---------------------------------------------------------------------------
# Multiple agents
# ---------------------------------------------------------------------------


def test_generate_multiple_agents_all_have_sections():
    gen = MarkdownReportGenerator()
    results = {
        "security": _make_result(),
        "data": _make_result(),
        "risk": _make_result(),
    }
    output = gen.generate(
        results=results,
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=AGENT_ORDER,
    )
    assert "## Security" in output
    assert "## Data" in output
    assert "## Risk" in output


def test_generate_respects_agent_type_order():
    gen = MarkdownReportGenerator()
    results = {
        "security": _make_result(),
        "data": _make_result(),
    }
    output = gen.generate(
        results=results,
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["data", "security"],
    )
    data_pos = output.index("## Data")
    security_pos = output.index("## Security")
    assert data_pos < security_pos


# ---------------------------------------------------------------------------
# Final summary section
# ---------------------------------------------------------------------------


def test_generate_includes_final_evaluation_summary():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result()},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "## Final Evaluation Summary" in output


def test_generate_includes_scorecard():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result()},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "### Cross-Category Scorecard" in output


def test_generate_includes_priority_actions():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result(ratings=["Red"])},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "### Priority Actions" in output


def test_generate_includes_overall_conclusion():
    gen = MarkdownReportGenerator()
    output = gen.generate(
        results={"security": _make_result()},
        document_title="Doc",
        section_labels=SECTION_LABELS,
        agent_type_order=["security"],
    )
    assert "### Overall Conclusion" in output


def test_classify_risk_high_when_red_present():
    gen = MarkdownReportGenerator()
    assert gen._classify_risk(red=1, amber=0) == "High Risk"


def test_classify_risk_medium_when_two_amber():
    gen = MarkdownReportGenerator()
    assert gen._classify_risk(red=0, amber=2) == "Medium Risk"


def test_classify_risk_low_when_no_issues():
    gen = MarkdownReportGenerator()
    assert gen._classify_risk(red=0, amber=0) == "Low Risk"


def test_top_finding_returns_first_red():
    gen = MarkdownReportGenerator()
    result = _make_result(ratings=["Green", "Red", "Amber"])
    top = gen._top_finding({"security": result}, ["security"])
    assert top == "Question 2"


def test_top_finding_returns_no_findings_when_all_green():
    gen = MarkdownReportGenerator()
    result = _make_result(ratings=["Green", "Green"])
    top = gen._top_finding({"security": result}, ["security"])
    assert top == "No findings"
