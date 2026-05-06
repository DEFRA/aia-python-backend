"""Agent Service — SQS polling loop for the ECS Fargate Agent Service.

Polls aia-tasks, dispatches each TaskMessage to the correct specialist agent,
fetches checklist questions from PostgreSQL, and publishes a StatusMessage to
aia-status. Messages are dispatched concurrently up to MAX_CONCURRENT_TASKS.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Evaluation module path injection — must precede 'src.*' imports below.
# The evaluation sub-package uses bare 'src.*' imports because its Lambda
# handler runs with app/agents/evaluation/ as the Python root.  We replicate
# that here so the worker can share the same agent/config/db code without
# duplicating it.
# ---------------------------------------------------------------------------
_EVAL_ROOT = Path(__file__).resolve().parent.parent / "agents" / "evaluation"
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

# Load the evaluation module's own .env so DatabaseConfig picks up DB_HOST/NAME/USER/PASSWORD.
# This must happen before any src.* import that triggers Pydantic settings initialisation.
from dotenv import load_dotenv as _load_dotenv  # noqa: E402

_load_dotenv(
    _EVAL_ROOT / ".env", override=False
)  # override=False: root .env values take precedence

from src.agents.schemas import (  # noqa: E402
    AgentLLMOutput,
    AgentResult,
    AssessmentRow,
    PolicyDocResult,
)
from src.config import DatabaseConfig  # noqa: E402
from src.utils.document_parser import _parse_bytes  # noqa: E402
from src.db.questions_repo import (  # noqa: E402
    fetch_all_policy_docs_by_category,
    fetch_questions_by_policy_doc_id,
)
from src.handlers.agent import AGENT_REGISTRY, CONFIG_REGISTRY  # noqa: E402
from src.utils.llm_client import make_llm_client  # noqa: E402

from app.core.config import config as app_config  # noqa: E402
from app.models.status_message import StatusMessage  # noqa: E402
from app.models.task_message import TaskMessage  # noqa: E402
from app.services.s3_service import S3Service  # noqa: E402
from app.services.sqs_service import SQSService  # noqa: E402
from app.utils.logger import get_logger  # noqa: E402

logger = get_logger("app.agent_service")


def _on_task_done(task: asyncio.Task) -> None:  # type: ignore[type-arg]
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.error("Unhandled exception in message processor", exc_info=exc)


async def _delete_with_retry(
    sqs: SQSService, queue_url: str, receipt: str, task_id: str
) -> None:
    for attempt in range(1, 4):
        try:
            await sqs.delete_message(queue_url, receipt)
            return
        except Exception as exc:
            if attempt == 3:
                raise
            logger.warning(
                "delete_message attempt %d/3 failed task_id=%s: %s — retrying in %ds",
                attempt,
                task_id,
                exc,
                attempt,
            )
            await asyncio.sleep(attempt)


# SQS visibility window — message stays invisible while the agent runs.
# Must be strictly greater than _AGENT_TIMEOUT_SECONDS so there is always
# time to publish an error StatusMessage before the message reappears.
_AGENT_VISIBILITY_TIMEOUT = 600

# Maximum time allowed for a single agent.assess() call.
# Sourced from AGENT_TIMEOUT_SECONDS (default 480 s).
# Kept below _AGENT_VISIBILITY_TIMEOUT to guarantee the error path completes.
_AGENT_TIMEOUT_SECONDS: int = app_config.orchestrator_agent_timeout

MAX_CONCURRENT_TASKS: int = int(os.environ.get("MAX_CONCURRENT_TASKS", "10"))

_db_config: DatabaseConfig | None = None


def _get_db_config() -> DatabaseConfig:
    global _db_config  # noqa: PLW0603
    if _db_config is None:
        _db_config = DatabaseConfig()
    return _db_config


def _extract_text(file_bytes: bytes, s3_key: str) -> str:
    """Extract plain text from PDF, DOCX, or UTF-8 text files."""
    lower = s3_key.lower()
    if lower.endswith(".pdf") or lower.endswith(".docx"):
        chunks = _parse_bytes(file_bytes, s3_key, "")
        return "\n\n".join(c["text"] for c in chunks if c.get("text"))
    return file_bytes.decode("utf-8", errors="replace")


async def _get_document(task: TaskMessage, s3: S3Service) -> str:
    """Return document text — inline from task or fetched from S3."""
    if task.file_content is not None:
        return task.file_content
    if task.s3_key is None or task.s3_bucket is None:
        raise ValueError(
            f"TaskMessage has no file_content and no s3_key/s3_bucket: task_id={task.task_id}"
        )
    file_bytes = await s3.download_file(task.s3_key, bucket=task.s3_bucket)
    return _extract_text(file_bytes, task.s3_key)


async def dispatch(task: TaskMessage, s3: S3Service) -> StatusMessage:
    """Run one agent task across ALL policy docs for the agent_type and return a StatusMessage.

    The Agent Service owns the per-policy-doc fan-out: it fetches every policy document
    for the given agent_type, runs assessments concurrently, and aggregates results into a
    single AgentResult.  Individual doc failures are logged and skipped rather than
    propagated, so a partial result is still publishable.  Infrastructure errors (DB down,
    S3 unavailable) propagate so the message stays invisible and retries via the DLQ path.
    """
    agent_type = task.agent_type

    if agent_type not in AGENT_REGISTRY:
        return StatusMessage(
            task_id=task.task_id,
            document_id=task.document_id,
            agent_type=agent_type,
            result={},
            error=f"Unknown agent type: {agent_type!r}",
        )

    document = await _get_document(task, s3)
    dsn = _get_db_config().dsn
    policy_docs = await fetch_all_policy_docs_by_category(dsn, agent_type)

    if not policy_docs:
        return StatusMessage(
            task_id=task.task_id,
            document_id=task.document_id,
            agent_type=agent_type,
            result={},
            error=f"No policy documents found for agent_type={agent_type!r}",
        )

    client = make_llm_client()
    agent_config = CONFIG_REGISTRY[agent_type]()
    agent = AGENT_REGISTRY[agent_type](client=client, agent_config=agent_config)

    async def _assess_one(
        policy_doc_id: str, policy_doc_url: str, policy_doc_filename: str
    ) -> PolicyDocResult | None:
        try:
            questions = await fetch_questions_by_policy_doc_id(dsn, policy_doc_id)
            llm_output: AgentLLMOutput = await asyncio.wait_for(
                agent.assess(document=document, questions=questions),
                timeout=_AGENT_TIMEOUT_SECONDS,
            )
            question_map = {q.id: q for q in questions}
            assessments: list[AssessmentRow] = []
            for row in llm_output.rows:
                q_item = question_map.get(row.question_id)
                if q_item is None:
                    raise ValueError(
                        f"LLM returned unknown question_id: {row.question_id}"
                    )
                assessments.append(
                    AssessmentRow(
                        Question=q_item.question,
                        Reference=q_item.reference,
                        Rating=row.Rating,
                        Comments=row.Comments,
                    )
                )
            return PolicyDocResult(
                policy_doc_filename=policy_doc_filename,
                policy_doc_url=policy_doc_url,
                assessments=assessments,
                summary=llm_output.summary,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Agent timed out after %ds — skipping doc task_id=%s policy_doc=%s",
                _AGENT_TIMEOUT_SECONDS,
                task.task_id,
                policy_doc_filename,
            )
            return None
        except Exception as exc:
            logger.error(
                "Agent assessment failed — skipping doc task_id=%s policy_doc=%s: %s",
                task.task_id,
                policy_doc_filename,
                exc,
            )
            return None

    raw_results = await asyncio.gather(
        *[_assess_one(pid, url, fname) for pid, url, fname in policy_docs]
    )
    docs = [r for r in raw_results if r is not None]

    if not docs:
        return StatusMessage(
            task_id=task.task_id,
            document_id=task.document_id,
            agent_type=agent_type,
            result={},
            error="All policy document assessments failed",
        )

    result = AgentResult(agent_type=agent_type, docs=docs)
    # TO DO: Handle scenario where result is too large for SQS message body limit (1 MiB)
    # potentially by uploading to S3 and including a link in the StatusMessage instead.
    return StatusMessage(
        task_id=task.task_id,
        document_id=task.document_id,
        agent_type=agent_type,
        result=result.model_dump(),
    )


async def run_worker() -> None:
    sqs = SQSService()
    s3 = S3Service()
    task_url = app_config.sqs.task_queue_url
    status_url = app_config.sqs.status_queue_url
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

    logger.info("Agent service started — polling %s", task_url)

    while True:
        try:
            messages = await sqs.receive_messages(
                task_url,
                max_messages=1,
                wait_seconds=20,
                visibility_timeout=_AGENT_VISIBILITY_TIMEOUT,
            )
            for msg in messages:
                t = asyncio.create_task(
                    _process_message(msg, sqs, s3, task_url, status_url, semaphore)
                )
                t.add_done_callback(_on_task_done)
        except asyncio.CancelledError:
            logger.info("Agent service stopped")
            return
        except Exception as exc:
            logger.exception("Worker poll error: %s", exc)
            await asyncio.sleep(5)


async def _process_message(
    msg: dict,
    sqs: SQSService,
    s3: S3Service,
    task_url: str,
    status_url: str,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        receipt = msg["receipt_handle"]
        try:
            task = TaskMessage.model_validate_json(msg["body"])
            logger.info(
                "Received task_id=%s agent_type=%s doc_id=%s policy_doc_id=%s",
                task.task_id,
                task.agent_type,
                task.document_id,
                task.policy_doc_id,
            )
            status = await dispatch(task, s3)
            await sqs.publish(status_url, status.model_dump_json(by_alias=True))
            await _delete_with_retry(sqs, task_url, receipt, task.task_id)
            logger.info(
                "Task complete task_id=%s error=%s",
                task.task_id,
                status.error,
            )
        except Exception as exc:
            logger.exception(
                "Unhandled task error — message not deleted (will retry): %s",
                exc,
            )
