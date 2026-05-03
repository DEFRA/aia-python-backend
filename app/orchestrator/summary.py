import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

_EVAL_ROOT = Path(__file__).resolve().parent.parent / "agents" / "evaluation"
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from src.agents.schemas import AgentResult, AssessmentRow  # noqa: E402


@runtime_checkable
class SummaryGenerator(Protocol):
    def generate(
        self,
        results: dict[str, AgentResult | None],
        document_title: str,
        section_labels: dict[str, str],
        agent_type_order: list[str],
    ) -> str: ...


class MarkdownReportGenerator:
    _RATING_EMOJI = {"Green": "🟢", "Amber": "🟡", "Red": "🔴"}

    def generate(
        self,
        results: dict[str, AgentResult | None],
        document_title: str,
        section_labels: dict[str, str],
        agent_type_order: list[str],
    ) -> str:
        lines: list[str] = [f"# {document_title}", ""]
        for agent_type in agent_type_order:
            result = results.get(agent_type)
            if result is None:
                continue
            label = section_labels.get(agent_type, agent_type.title())
            lines.extend(self._render_category_section(result, label))
            lines.append("---")
            lines.append("")
        lines.extend(
            self._render_final_summary(results, section_labels, agent_type_order)
        )
        return "\n".join(lines)

    def _render_category_section(self, result: AgentResult, label: str) -> list[str]:
        lines = [f"## {label}", ""]
        lines.append(f"### {result.policy_doc_filename}")
        lines.append(f"[View document]({result.policy_doc_url})")
        lines.append("")
        lines.append("| Question | Reference | Rating | Comments |")
        lines.append("|---|---|---|---|")
        for row in result.assessments:
            emoji = self._RATING_EMOJI.get(row.Rating, "")
            q = row.Question.replace("|", "\\|")
            c = row.Comments.replace("|", "\\|")
            lines.append(f"| {q} | {row.Reference} | {emoji} {row.Rating} | {c} |")
        lines.append("")
        lines.append("**Summary**")
        lines.append(
            f"{result.summary.Interpretation} — {result.summary.Overall_Comments}"
        )
        lines.append("")
        return lines

    def _render_final_summary(
        self,
        results: dict[str, AgentResult | None],
        section_labels: dict[str, str],
        agent_type_order: list[str],
    ) -> list[str]:
        lines = ["## Final Evaluation Summary", ""]
        lines.append("### Cross-Category Scorecard")
        lines.append("")
        lines.append("| Category | 🟢 Green | 🟡 Amber | 🔴 Red | Score |")
        lines.append("|---|---|---|---|---|")
        total_g = total_a = total_r = 0
        for agent_type in agent_type_order:
            result = results.get(agent_type)
            if result is None:
                continue
            label = section_labels.get(agent_type, agent_type.title())
            g = sum(1 for r in result.assessments if r.Rating == "Green")
            a = sum(1 for r in result.assessments if r.Rating == "Amber")
            r = sum(1 for r in result.assessments if r.Rating == "Red")
            total = g + a + r
            score = round((g / total) * 100) if total > 0 else 0
            lines.append(f"| {label} | {g} | {a} | {r} | {score}% |")
            total_g += g
            total_a += a
            total_r += r
        total_all = total_g + total_a + total_r
        overall_score = round((total_g / total_all) * 100) if total_all > 0 else 0
        lines.append(
            f"| **Overall** | **{total_g}** | **{total_a}** | **{total_r}** | **{overall_score}%** |"
        )
        lines.append("")
        lines.append("### Priority Actions")
        lines.append("")
        priority: list[tuple[str, AssessmentRow]] = []
        for agent_type in agent_type_order:
            result = results.get(agent_type)
            if result is None:
                continue
            label = section_labels.get(agent_type, agent_type.title())
            for row in result.assessments:
                if row.Rating in ("Red", "Amber"):
                    priority.append((label, row))
        priority.sort(key=lambda x: (0 if x[1].Rating == "Red" else 1, x[0]))
        for i, (label, row) in enumerate(priority, 1):
            emoji = self._RATING_EMOJI.get(row.Rating, "")
            lines.append(
                f"{i}. {emoji} **{label}** — {row.Question} *({row.Reference})*"
            )
        lines.append("")
        lines.append("### Overall Conclusion")
        lines.append("")
        risk = self._classify_risk(total_r, total_a)
        weakest = self._weakest_category(results, section_labels, agent_type_order)
        top = self._top_finding(results, agent_type_order)
        n_red = total_r
        n_categories = sum(1 for at in agent_type_order if results.get(at) is not None)
        lines.append(
            f"The assessment reviewed {total_all} controls across {n_categories} policy areas. "
            f"Overall compliance stands at **{overall_score}% — {risk}** "
            f"({total_g} Green, {total_a} Amber, {total_r} Red). "
            f"{weakest} is the weakest area with {n_red} critical gap(s). "
            f'Most urgent finding: *"{top}"* — immediate remediation required.'
        )
        lines.append("")
        return lines

    def _classify_risk(self, red: int, amber: int) -> str:
        if red > 0:
            return "High Risk"
        if amber >= 2:
            return "Medium Risk"
        return "Low Risk"

    def _weakest_category(
        self,
        results: dict[str, AgentResult | None],
        section_labels: dict[str, str],
        agent_type_order: list[str],
    ) -> str:
        worst_label = ""
        worst_score = -1
        for agent_type in agent_type_order:
            result = results.get(agent_type)
            if result is None:
                continue
            bad = sum(1 for r in result.assessments if r.Rating in ("Red", "Amber"))
            if bad > worst_score:
                worst_score = bad
                worst_label = section_labels.get(agent_type, agent_type.title())
        return worst_label or "Unknown"

    def _top_finding(
        self,
        results: dict[str, AgentResult | None],
        agent_type_order: list[str],
    ) -> str:
        for rating in ("Red", "Amber"):
            for agent_type in agent_type_order:
                result = results.get(agent_type)
                if result is None:
                    continue
                for row in result.assessments:
                    if row.Rating == rating:
                        return row.Question
        return "No findings"
