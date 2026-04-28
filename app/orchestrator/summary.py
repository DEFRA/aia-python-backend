from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SummaryGenerator(Protocol):
    def generate(self, results: dict[str, Any]) -> str: ...


class MarkdownSummaryGenerator:
    """
    Assembles per-agent results into a single Markdown assessment report.

    Keys in `results` are task IDs of the form "{documentId}_{agentType}".
    The agentType suffix is used as the section heading.
    """

    def generate(self, results: dict[str, Any]) -> str:
        if not results:
            return ""

        sections = ["# AI Assessment Report\n"]
        for task_id, result in sorted(results.items()):
            agent_name = self._agent_name(task_id)
            sections.append(f"\n## {agent_name}\n")
            sections.append(self._format_result(result))

        return "\n".join(sections)

    @staticmethod
    def _agent_name(task_id: str) -> str:
        parts = task_id.rsplit("_", 1)
        raw = parts[1] if len(parts) == 2 else task_id
        return raw.replace("-", " ").title()

    @staticmethod
    def _format_result(result: Any) -> str:
        if not isinstance(result, dict):
            return str(result)
        lines = []
        for key, value in result.items():
            heading = key.replace("_", " ").title()
            if isinstance(value, list):
                lines.append(f"\n**{heading}:**")
                lines.extend(f"- {item}" for item in value)
            elif isinstance(value, dict):
                lines.append(f"\n**{heading}:**")
                for sub_key, sub_val in value.items():
                    lines.append(f"  - {sub_key}: {sub_val}")
            else:
                lines.append(f"\n**{heading}:** {value}")
        return "\n".join(lines)
