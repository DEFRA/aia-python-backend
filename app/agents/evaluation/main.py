"""Entry point: run the Security Assessment Agent against a document file."""
import asyncio
import logging
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src.agents.security_agent import SecurityAgent
from src.agents.schemas import AgentResult
from src.config import DatabaseConfig, SecurityAgentConfig
from src.db.questions_repo import fetch_questions_by_category
from src.utils.pdf_creator_multipage import build_security_report

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger: logging.Logger = logging.getLogger(__name__)



def _build_dataset(result: AgentResult) -> dict[str, dict[str, object]]:
    """Convert an AgentResult into the dict structure expected by build_security_report.

    Args:
        result: The structured assessment result from SecurityAgent.

    Returns:
        A dict with a single "Security" key containing "Assessments" and "Final_Summary".
    """
    return {
        "Security": {
            "Assessments": [row.model_dump() for row in result.assessments],
            "Final_Summary": result.final_summary.model_dump() if result.final_summary else {},
        }
    }


async def run(document_path: str, output_pdf: str, category: str = "Security") -> None:
    """Orchestrate the full assessment pipeline: load → fetch questions → assess → write PDF.

    Args:
        document_path: Path to the document to assess.
        output_pdf: Destination path for the generated PDF report.
        category: Checklist category to query from the database (default: "Security").
    """
    document: str = Path(document_path).read_text(encoding="utf-8")

    db_config: DatabaseConfig = DatabaseConfig()
    questions: list[str] = await fetch_questions_by_category(
        dsn=db_config.dsn,
        category=category,
    )

    config: SecurityAgentConfig = SecurityAgentConfig()
    client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic(api_key=config.api_key or None)
    agent: SecurityAgent = SecurityAgent(client=client, agent_config=config)

    logger.info("Running assessment on '%s' (%d questions)...", document_path, len(questions))
    result: AgentResult = await agent.assess(document=document, questions=questions)

    dataset: dict[str, dict[str, object]] = _build_dataset(result)
    build_security_report(datasets=[dataset], output_path=output_pdf)
    logger.info("PDF written to %s", output_pdf)

    if result.final_summary:
        logger.info("Interpretation: %s", result.final_summary.Interpretation)
        logger.info("Comments:       %s", result.final_summary.Overall_Comments)


if __name__ == "__main__":
    doc: str = sys.argv[1] if len(sys.argv) > 1 else "files/security_policy.md"
    output: str = sys.argv[2] if len(sys.argv) > 2 else "files/security_assessment_report.pdf"
    cat: str = sys.argv[3] if len(sys.argv) > 3 else "Security"
    asyncio.run(run(doc, output, cat))
