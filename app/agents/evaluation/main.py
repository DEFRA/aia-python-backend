"""Local end-to-end runner for the evaluation pipeline.

Mocks the SQS Tasks queue input from ``files/system_input_output_SQS.md`` --
``{"document_id": "...","s3Key": "..."}`` -- and drives every pipeline stage in
sequence against the underlying business logic:

    Stage 3 Parse -> Stage 4 Tag -> Stage 5 Extract Sections -> Stage 6 Agent

AWS infrastructure (S3, SQS, EventBridge, CloudWatch) is bypassed entirely:
the ``s3Key`` is resolved as a path relative to this directory and read from
disk; per-stage state is passed in-process; the terminal SQS Status message
is written as JSON into the configured data folder.

All operational defaults (data folder, default input filename, output
filename template, agent-type display keys) are sourced from
``LocalRunnerConfig`` -- see ``config.yaml`` -> ``local_runner``. No
hardcoded paths or display strings live in this module.

The output JSON shape matches the contract in ``files/system_input_output_SQS.md``:
``{"document_id": "...","<Section>": {"Assessments": [...], "Final_Summary": {...}}}``
with one section per specialist agent that has an assessment input file.
"""

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import anthropic
from dotenv import load_dotenv

from src.agents.schemas import AgentResult, TaggedChunk
from src.agents.tagging_agent import TaggingAgent
from src.config import DatabaseConfig, LocalRunnerConfig, PipelineConfig, TaggingAgentConfig
from src.db.questions_repo import fetch_assessment_by_category
from src.handlers.agent import (
    AGENT_REGISTRY,
    CONFIG_REGISTRY,
    AgentTaskBody,
    SpecialistAgent,
    SpecialistAgentConfig,
)
from src.handlers.extract_sections import extract_sections_for_agent
from src.handlers.parse import SqsRecordBody, _parse_bytes
from src.utils.exceptions import UnknownCategoryError
from src.utils.llm_client import make_llm_client

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger: logging.Logger = logging.getLogger(__name__)

_EVAL_DIR: Path = Path(__file__).resolve().parent


def _sections_to_text(sections: list[dict[str, Any]]) -> str:
    """Serialise filtered tagged chunks into the plain-text form Stage 6 expects.

    Mirrors ``src.handlers.extract_sections._sections_to_text`` -- headings
    are prefixed with ``## ``, body chunks are separated by blank lines.

    Args:
        sections: List of tagged-chunk dicts produced by
            :func:`extract_sections_for_agent`.

    Returns:
        Plain-text concatenation suitable for the agent's ``document`` arg.
    """
    lines: list[str] = []
    for chunk in sections:
        if chunk.get("is_heading"):
            lines.append(f"## {chunk['text']}")
        else:
            lines.append(chunk["text"])
    return "\n\n".join(lines)


def _resolve_local_path(s3_key: str) -> Path:
    """Resolve an ``s3Key`` to a local file path under the evaluation directory.

    Accepts either Windows-style backslashes or forward slashes in *s3_key*.

    Args:
        s3_key: Path-like key as it would appear in the SQS Tasks body.

    Returns:
        The absolute local path the runner will read bytes from.

    Raises:
        FileNotFoundError: If the resolved path is not a regular file.
    """
    normalised: str = s3_key.replace("\\", "/")
    path: Path = (_EVAL_DIR / normalised).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Document not found at {path} (s3Key={s3_key!r})")
    return path


async def run_pipeline(s3_key: str, doc_id: str, output_path: Path) -> dict[str, Any]:
    """Drive the four pipeline stages end-to-end against a local document.

    Args:
        s3_key: Path-like key from the mock SQS Tasks body. Resolved locally.
        doc_id: Document identifier echoed into every stage's payload.
        output_path: Destination JSON file for the combined SQS Status output.

    Returns:
        The combined output dict that was written to ``output_path``.

    Raises:
        FileNotFoundError: If the document at ``s3_key`` does not exist locally.
        RuntimeError: If no specialist agent has a matching assessment input file.
    """
    runner_config: LocalRunnerConfig = LocalRunnerConfig()

    # ---- Mock Stage 3 input: SQS Tasks message body --------------------
    sqs_body: SqsRecordBody = SqsRecordBody(document_id=doc_id, s3Key=s3_key)
    logger.info("Mock SQS Tasks body: %s", sqs_body.model_dump_json())

    # ---- Stage 3 -- Parse: read bytes from local disk, parse to chunks --
    file_path: Path = _resolve_local_path(s3_key)
    file_bytes: bytes = file_path.read_bytes()
    chunks: list[dict[str, Any]] = _parse_bytes(file_bytes, s3_key, doc_id)
    logger.info("Stage 3 Parse: %d chunks from %s", len(chunks), file_path.name)

    # ---- Stage 4 -- Tag: TaggingAgent over the chunks ------------------
    # Local mode: route through AWS Bedrock so the runner picks up AWS_*
    # credentials from .env via the boto3 default chain. The agents type
    # ``client`` as ``anthropic.AsyncAnthropic``; ``AsyncAnthropicBedrock``
    # is duck-compatible (same ``messages.create`` interface).
    tagging_config: TaggingAgentConfig = TaggingAgentConfig()  # type: ignore[call-arg]
    tagging_client: anthropic.AsyncAnthropic = make_llm_client()
    tagging_agent: TaggingAgent = TaggingAgent(client=tagging_client, config=tagging_config)
    tagged_chunks: list[TaggedChunk] = await tagging_agent.tag(chunks)
    tagged_dicts: list[dict[str, Any]] = [tc.model_dump() for tc in tagged_chunks]
    logger.info("Stage 4 Tag: %d tagged chunks", len(tagged_dicts))

    # ---- Stage 5 -- Extract sections + per-agent fan-out ---------------
    pipeline_config: PipelineConfig = PipelineConfig()
    enqueued_at: str = datetime.now(tz=UTC).isoformat()
    agent_tasks: list[AgentTaskBody] = []

    for agent_type in pipeline_config.agent_types:
        display_key: str | None = runner_config.display_keys.get(agent_type)
        if display_key is None:
            logger.warning(
                "No display_key mapping for agent_type=%s -- skipping.",
                agent_type,
            )
            continue

        try:
            questions, category_url = await fetch_assessment_by_category(
                DatabaseConfig().dsn,  # type: ignore[call-arg]
                display_key,
            )
        except UnknownCategoryError:
            logger.warning(
                "No assessment input for '%s' (looked for category=%s) -- skipping this agent.",
                agent_type,
                display_key,
            )
            continue

        sections: list[dict[str, Any]] = extract_sections_for_agent(tagged_dicts, agent_type)
        document_text: str = _sections_to_text(sections)

        body: AgentTaskBody = AgentTaskBody(
            document_id=doc_id,
            agentType=agent_type,
            document=document_text,
            questions=questions,
            categoryUrl=category_url,
            enqueuedAt=enqueued_at,
        )
        agent_tasks.append(body)
        logger.info(
            "Stage 5 ExtractSections: agent=%s sections=%d questions=%d chars=%d",
            agent_type,
            len(sections),
            len(questions),
            len(document_text),
        )

    if not agent_tasks:
        raise RuntimeError(
            "No specialist agents to run -- no assessment input matched any "
            f"configured agent type ({pipeline_config.agent_types})."
        )

    # ---- Stage 6 -- Agent: run each specialist on its sections ---------
    output: dict[str, Any] = {"document_id": doc_id}

    for body in agent_tasks:
        agent_type = body.agentType
        agent_config: SpecialistAgentConfig = CONFIG_REGISTRY[agent_type]()
        agent_client: anthropic.AsyncAnthropic = make_llm_client()
        agent: SpecialistAgent = AGENT_REGISTRY[agent_type](
            client=agent_client,
            agent_config=agent_config,
        )

        logger.info("Stage 6 Agent: running %s assessment...", agent_type)
        result: AgentResult = await agent.assess(
            document=body.document or "",
            questions=body.questions,
            category_url=body.categoryUrl,
        )

        section_key: str = runner_config.display_keys[agent_type]
        output[section_key] = {
            "Assessments": [row.model_dump() for row in result.assessments],
            "Final_Summary": (result.final_summary.model_dump() if result.final_summary else {}),
        }
        logger.info(
            "Stage 6 complete: agent=%s assessments=%d input_tokens=%d output_tokens=%d",
            agent_type,
            len(result.assessments),
            result.metadata.input_tokens,
            result.metadata.output_tokens,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Pipeline output written to %s", output_path)
    return output


if __name__ == "__main__":
    runner_config: LocalRunnerConfig = LocalRunnerConfig()
    data_dir: Path = _EVAL_DIR / runner_config.data_dir

    s3_key_arg: str = (
        sys.argv[1]
        if len(sys.argv) > 1
        else f"{runner_config.data_dir}/{runner_config.default_input_filename}"
    )
    doc_id_arg: str = sys.argv[2] if len(sys.argv) > 2 else f"UUID-{uuid4()}"
    output_arg: Path = (
        Path(sys.argv[3])
        if len(sys.argv) > 3
        else data_dir / runner_config.output_filename_template.format(doc_id=doc_id_arg)
    )

    asyncio.run(run_pipeline(s3_key_arg, doc_id_arg, output_arg))
