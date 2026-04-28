"""Entry point: run a specialist assessment agent against a document file.

Local development pattern: build a real ``AgentTaskBody`` (so the SQS message-
body schema is exercised end-to-end) then dispatch to the agent class via
``AGENT_REGISTRY`` and call its ``assess()`` directly. When real SQS lands, the
direct call is replaced with ``boto3.send_message(MessageBody=body.model_dump_json())``.

Supported categories: ``Security`` and ``Governance`` -- the two surviving
specialist agents after Plan 10's roster reduction.
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
from src.db.assessment_loader import load_assessment_from_file
from src.handlers.agent import (
    AGENT_REGISTRY,
    CONFIG_REGISTRY,
    AgentTaskBody,
    SpecialistAgent,
    SpecialistAgentConfig,
)
from src.utils.pdf_creator_multipage import build_security_report

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger: logging.Logger = logging.getLogger(__name__)

_DISPLAY_KEYS: dict[str, str] = {
    "security": "Security",
    "governance": "Governance",
}


def _build_dataset(result: AgentResult, agent_type: str) -> dict[str, object]:
    """Convert an AgentResult into the dict structure expected by build_security_report.

    Args:
        result: The structured assessment result from the specialist agent.
        agent_type: The lower-case agent identifier (``"security"``, ``"governance"``).

    Returns:
        A dict whose single top-level key matches the agent's section name
        (``"Security"`` or ``"Governance"``) and contains ``"Assessments"``
        and ``"Final_Summary"``.
    """
    section: str = _DISPLAY_KEYS[agent_type]
    return {
        section: {
            "Assessments": [row.model_dump() for row in result.assessments],
            "Final_Summary": result.final_summary.model_dump() if result.final_summary else {},
        }
    }


async def run(document_path: str, output_pdf: str, category: str = "Security") -> None:
    """Orchestrate the full assessment pipeline: load -> build body -> assess -> write PDF.

    Args:
        document_path: Path to the document to assess.
        output_pdf: Destination path for the generated PDF report.
        category: Checklist category to load from the data directory.
            Must match a registered specialist agent (``Security`` or ``Governance``).

    Raises:
        ValueError: If ``category`` does not map to a registered agent type.
    """
    document: str = Path(document_path).read_text(encoding="utf-8")

    questions, category_url = load_assessment_from_file(category)

    agent_type: str = category.lower()
    if agent_type not in AGENT_REGISTRY:
        raise ValueError(
            f"Unsupported category '{category}'. "
            f"Supported categories: {sorted(_DISPLAY_KEYS.values())}"
        )

    # Build the SQS Tasks message body even though we run in-process. This
    # exercises the new contract (Pydantic boundary validation) end-to-end so
    # local runs catch any drift from the production schema.
    body: AgentTaskBody = AgentTaskBody(
        docId=str(uuid4()),
        agentType=agent_type,
        document=document,
        questions=questions,
        categoryUrl=category_url,
        enqueuedAt=datetime.now(tz=UTC).isoformat(),
    )

    agent_config: SpecialistAgentConfig = CONFIG_REGISTRY[agent_type]()
    client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic(
        api_key=agent_config.api_key or None,
    )
    agent: SpecialistAgent = AGENT_REGISTRY[agent_type](
        client=client,
        agent_config=agent_config,
    )

    logger.info(
        "Running %s assessment on '%s' (%d questions)...",
        category,
        document_path,
        len(body.questions),
    )
    # Local mode: skip the queue, call assess() directly with the validated body.
    result: AgentResult = await agent.assess(
        document=body.document or "",
        questions=body.questions,
        category_url=body.categoryUrl,
    )

    dataset: dict[str, object] = _build_dataset(result, agent_type)
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
