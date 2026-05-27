"""Agent Service — SQS polling loop for the ECS Fargate Agent Service.

Polls aia-tasks, dispatches each TaskMessage to the correct specialist agent,
fetches checklist questions from PostgreSQL, and publishes a StatusMessage to
aia-status. Messages are dispatched concurrently up to MAX_CONCURRENT_TASKS.
"""

from __future__ import annotations

import asyncio
import os

from pydantic import ValidationError

from app.agent_service.src.models.schemas import (
    AgentResult,
    AssessmentRow,
    PolicyDocResult,
)
from app.agent_service.src.config import DatabaseConfig
from app.agent_service.src.utils.doc_parser import _parse_bytes
from app.agent_service.src.repositories.questions_repo import (
    fetch_all_policy_docs_by_category,
    fetch_questions_by_policy_doc_id,
)
from app.agent_service.src.handlers.agent import AGENT_REGISTRY, CONFIG_REGISTRY
from app.agent_service.src.utils.llm_client import make_llm_client

from app.agent_service.src.shared.app_config import config as app_config
from app.agent_service.src.shared.status_message import StatusMessage
from app.agent_service.src.shared.task_message import TaskMessage
from app.agent_service.src.shared.s3_service import S3Service
from app.agent_service.src.shared.sqs_service import SQSService
from app.agent_service.src.shared.logger import get_logger

logger = get_logger("app.agent_service")

# References: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.html
_MAX_SQS_MESSAGE_BYTES = 1024 * 1024


class NonRetriableTaskMessageError(ValueError):
    """Raised when a task payload is malformed or violates invariants."""


def _parse_task_message(body: str) -> TaskMessage:
    """Parse and validate a raw task queue payload.

    Raises NonRetriableTaskMessageError for malformed or poison messages so they can
    be deleted immediately instead of retried forever.
    """
    if len(body.encode("utf-8")) > _MAX_SQS_MESSAGE_BYTES:
        raise NonRetriableTaskMessageError("Task message exceeds SQS payload limit")

    try:
        task = TaskMessage.model_validate_json(body)
    except ValidationError as exc:
        raise NonRetriableTaskMessageError("Task payload validation failed") from exc

    expected_task_id = f"{task.document_id}_{task.agent_type}"
    if task.task_id != expected_task_id:
        raise NonRetriableTaskMessageError(
            "task_id must match {document_id}_{agent_type}"
        )

    if task.agent_type not in AGENT_REGISTRY:
        raise NonRetriableTaskMessageError("Unknown agent_type in task payload")

    return task


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
_AGENT_VISIBILITY_TIMEOUT = 600

# Maximum time allowed for a single agent.assess() call.
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
    if lower.endswith((".pdf", ".docx")):
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


async def _assess_one_doc(
    agent,
    document: str,
    dsn: str,
    policy_doc_id: str,
    policy_doc_url: str,
    policy_doc_filename: str,
    task_id: str,
    agent_type: str,
) -> tuple[PolicyDocResult | None, dict[str, int]]:
    """Assess one policy doc and return (result, token_counts)."""
    tokens = {"input_tokens": 0, "output_tokens": 0}
    try:
        questions = await fetch_questions_by_policy_doc_id(dsn, policy_doc_id)
        llm_output = await asyncio.wait_for(
            agent.assess(document=document, questions=questions),
            timeout=_AGENT_TIMEOUT_SECONDS,
        )

        if llm_output.llm_meta is not None:
            try:
                tokens["input_tokens"] = llm_output.llm_meta.input_tokens
                tokens["output_tokens"] = llm_output.llm_meta.output_tokens
            except AttributeError:
                logger.exception(
                    "[TOKEN] Missing token attributes in llm_meta task_id=%s agent_type=%s doc=%s",
                    task_id,
                    agent_type,
                    policy_doc_filename,
                )

        logger.info(
            "[TOKEN] Agent assessment complete — task_id=%s agent_type=%s doc=%s | input_tokens=%d output_tokens=%d",
            task_id,
            agent_type,
            policy_doc_filename,
            tokens["input_tokens"],
            tokens["output_tokens"],
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
        result = PolicyDocResult(
            policy_doc_filename=policy_doc_filename,
            policy_doc_url=policy_doc_url,
            assessments=assessments,
            summary=llm_output.summary,
        )
        return (result, tokens)
    except asyncio.TimeoutError:
        logger.error(
            "[TOKEN] Agent timed out after %ds — skipping doc task_id=%s policy_doc=%s",
            _AGENT_TIMEOUT_SECONDS,
            task_id,
            policy_doc_filename,
        )
        return (None, tokens)
    except Exception:
        logger.exception(
            "[TOKEN] Agent assessment failed — skipping doc task_id=%s policy_doc=%s",
            task_id,
            policy_doc_filename,
        )
        return (None, tokens)


def _aggregate_results(
    raw_results: list[tuple[PolicyDocResult | None, dict[str, int]]],
) -> tuple[list[PolicyDocResult], int, int]:
    """Aggregate successful results and sum token counts."""
    docs = []
    total_input_tokens = 0
    total_output_tokens = 0
    for doc_result, tokens in raw_results:
        if doc_result is not None:
            docs.append(doc_result)
            total_input_tokens += tokens.get("input_tokens", 0)
            total_output_tokens += tokens.get("output_tokens", 0)
    return docs, total_input_tokens, total_output_tokens


async def dispatch(task: TaskMessage, s3: S3Service) -> StatusMessage:
    """Run one agent task across ALL policy docs for the agent_type and return a StatusMessage."""
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

    raw_results = await asyncio.gather(
        *[
            _assess_one_doc(
                agent, document, dsn, pid, url, fname,
                task.task_id, agent_type,
            )
            for pid, url, fname in policy_docs
        ]
    )

    docs, total_input_tokens, total_output_tokens = _aggregate_results(raw_results)

    logger.info(
        "[TOKEN] Aggregated tokens for all docs — task_id=%s agent_type=%s model_id=%s | total_input_tokens=%d total_output_tokens=%d docs_assessed=%d",
        task.task_id,
        agent_type,
        agent_config.model,
        total_input_tokens,
        total_output_tokens,
        len(docs),
    )

    if not docs:
        logger.warning(
            "[TOKEN] All policy document assessments failed — task_id=%s agent_type=%s",
            task.task_id,
            agent_type,
        )
        return StatusMessage(
            task_id=task.task_id,
            document_id=task.document_id,
            agent_type=agent_type,
            result={},
            error="All policy document assessments failed",
            model_id=agent_config.model,
            input_tokens=None,
            output_tokens=None,
        )

    result = AgentResult(agent_type=agent_type, docs=docs)
    status_msg = StatusMessage(
        task_id=task.task_id,
        document_id=task.document_id,
        agent_type=agent_type,
        result=result.model_dump(),
        model_id=agent_config.model,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
    )

    logger.info(
        "[TOKEN] StatusMessage published — task_id=%s agent_type=%s model_id=%s | inputTokens=%d outputTokens=%d",
        task.task_id,
        agent_type,
        agent_config.model,
        total_input_tokens,
        total_output_tokens,
    )

    return status_msg


async def run_worker() -> None:
    sqs = SQSService()
    s3 = S3Service()
    task_url = app_config.sqs.task_queue_url
    status_url = app_config.sqs.status_queue_url
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

    logger.info("Agent service started — polling task queue")

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
            raise
        except Exception:
            logger.exception("Worker poll error")
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
        body = msg.get("body", "")
        try:
            task = _parse_task_message(body)
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
        except NonRetriableTaskMessageError as exc:
            preview = body[:200].replace("\n", "\\n")
            logger.warning(
                "Discarding poison task message and deleting from queue: %s | body_preview=%s",
                exc,
                preview,
            )
            try:
                await _delete_with_retry(
                    sqs,
                    task_url,
                    receipt,
                    task_id="invalid_task_message",
                )
            except Exception:
                logger.exception("Failed to delete poison task message")
        except Exception as exc:
            logger.exception(
                "Unhandled task error — message not deleted (will retry): %s",
                exc,
            )
