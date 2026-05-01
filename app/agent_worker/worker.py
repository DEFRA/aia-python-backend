"""Agent Worker — SQS polling loop for the ECS Fargate Agent Service.

Polls aia-tasks, dispatches each TaskMessage to the correct specialist agent,
fetches checklist questions from PostgreSQL, and publishes a StatusMessage to
aia-status. One message is processed at a time; visibility timeout is set to
600 s to cover the maximum expected LLM call duration.
"""

from __future__ import annotations

import asyncio
import logging
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

from src.config import DatabaseConfig  # noqa: E402
from src.db.questions_repo import fetch_assessment_by_category  # noqa: E402
from src.handlers.agent import AGENT_REGISTRY, CONFIG_REGISTRY  # noqa: E402
from src.utils.llm_client import make_llm_client  # noqa: E402

from app.core.config import config as app_config
from app.models.status_message import StatusMessage
from app.models.task_message import TaskMessage
from app.services.s3_service import S3Service
from app.services.sqs_service import SQSService
from app.utils.logger import get_logger

logger = get_logger("app.agent_worker")

# Covers the maximum expected LLM call; set on the receive call so the message
# stays invisible while the agent runs.
_AGENT_VISIBILITY_TIMEOUT = 600

_db_config: DatabaseConfig | None = None


def _get_db_config() -> DatabaseConfig:
    global _db_config  # noqa: PLW0603
    if _db_config is None:
        _db_config = DatabaseConfig()
    return _db_config


async def _get_document(task: TaskMessage, s3: S3Service) -> str:
    """Return document text — inline from task or fetched from S3."""
    if task.file_content is not None:
        return task.file_content
    file_bytes = await s3.download_file(task.s3_key, bucket=task.s3_bucket)
    return file_bytes.decode("utf-8")


async def dispatch(task: TaskMessage, s3: S3Service) -> StatusMessage:
    """Run one agent task and return a StatusMessage (success or failure).

    Agent errors are caught and surfaced as a StatusMessage with error set,
    rather than propagating, so the caller always publishes one result per task.
    Infrastructure errors (DB down, S3 unavailable) propagate so the caller
    can leave the message invisible and let it retry via the DLQ path.
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
    questions, category_url = await fetch_assessment_by_category(dsn, agent_type)

    client = make_llm_client()
    agent_config = CONFIG_REGISTRY[agent_type]()
    agent = AGENT_REGISTRY[agent_type](client=client, agent_config=agent_config)

    try:
        result = await agent.assess(
            document=document,
            questions=questions,
            category_url=category_url,
        )
        return StatusMessage(
            task_id=task.task_id,
            document_id=task.document_id,
            agent_type=agent_type,
            result=result.model_dump(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Agent assessment failed task_id=%s agent_type=%s: %s",
            task.task_id,
            agent_type,
            exc,
        )
        return StatusMessage(
            task_id=task.task_id,
            document_id=task.document_id,
            agent_type=agent_type,
            result={},
            error=str(exc),
        )


async def run_worker() -> None:
    """Main SQS polling loop — runs until cancelled.

    Each iteration receives at most one message, dispatches it synchronously,
    publishes the StatusMessage, then deletes the task message.  If dispatch
    raises (infrastructure failure), the message is left in-flight so its
    visibility timeout expires and SQS retries it (up to maxReceiveCount
    before routing to the DLQ).
    """
    sqs = SQSService()
    s3 = S3Service()
    task_url = app_config.sqs.task_queue_url
    status_url = app_config.sqs.status_queue_url

    logger.info("Agent worker started — polling %s", task_url)

    while True:
        try:
            messages = await sqs.receive_messages(
                task_url,
                max_messages=1,
                wait_seconds=20,
                visibility_timeout=_AGENT_VISIBILITY_TIMEOUT,
            )
            for msg in messages:
                receipt = msg["receipt_handle"]
                try:
                    task = TaskMessage.model_validate_json(msg["body"])
                    logger.info(
                        "Received task_id=%s agent_type=%s doc_id=%s",
                        task.task_id,
                        task.agent_type,
                        task.document_id,
                    )
                    status = await dispatch(task, s3)
                    await sqs.publish(status_url, status.model_dump_json(by_alias=True))
                    await sqs.delete_message(task_url, receipt)
                    logger.info(
                        "Task complete task_id=%s error=%s",
                        task.task_id,
                        status.error,
                    )
                except Exception as exc:
                    logger.exception(
                        "Unhandled task error — message not deleted (will retry): %s", exc
                    )
        except asyncio.CancelledError:
            logger.info("Agent worker stopped")
            return
        except Exception as exc:
            logger.exception("Worker poll error: %s", exc)
            await asyncio.sleep(5)
