"""Entry point: run the Security Assessment Agent against a document file.

Local development pattern: build a real ``AgentTaskBody`` (so the SQS message-
body schema is exercised end-to-end) then call ``SecurityAgent.assess()``
directly. When real SQS lands, the direct call is replaced with
``boto3.send_message(MessageBody=body.model_dump_json())``.
"""

import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import anthropic
from dotenv import load_dotenv

from src.agents.schemas import AgentResult
from src.agents.security_agent import SecurityAgent
from src.config import SecurityAgentConfig
from src.db.assessment_loader import load_assessment_from_file
from src.handlers.agent import AgentTaskBody
from src.utils.pdf_creator_multipage import build_security_report

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger: logging.Logger = logging.getLogger(__name__)


def _build_dataset(result: AgentResult) -> dict[str, object]:
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
    """Orchestrate the full assessment pipeline: load -> build body -> assess -> write PDF.

    Args:
        document_path: Path to the document to assess.
        output_pdf: Destination path for the generated PDF report.
        category: Checklist category to load from the data directory (default: "Security").
    """
    document: str = Path(document_path).read_text(encoding="utf-8")

    questions, category_url = load_assessment_from_file(category)

    # Build the SQS Tasks message body even though we run in-process. This
    # exercises the new contract (Pydantic boundary validation) end-to-end so
    # local runs catch any drift from the production schema.
    body: AgentTaskBody = AgentTaskBody(
        docId=str(uuid4()),
        agentType=category.lower(),
        document=document,
        questions=questions,
        categoryUrl=category_url,
        enqueuedAt=datetime.now(tz=UTC).isoformat(),
    )

    config: SecurityAgentConfig = SecurityAgentConfig()
    client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic(api_key=config.api_key or None)
    agent: SecurityAgent = SecurityAgent(client=client, agent_config=config)

    logger.info(
        "Running assessment on '%s' (%d questions)...",
        document_path,
        len(body.questions),
    )
    # Local mode: skip the queue, call assess() directly with the validated body.
    result: AgentResult = await agent.assess(
        document=body.document or "",
        questions=body.questions,
        category_url=body.categoryUrl,
    )

    dataset: dict[str, object] = _build_dataset(result)
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
