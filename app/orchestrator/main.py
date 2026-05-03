import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI

_EVAL_ROOT = Path(__file__).resolve().parent.parent / "agents" / "evaluation"
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from src.agents.schemas import AgentResult  # noqa: E402
from src.config import PipelineConfig  # noqa: E402

from app.core.config import config  # noqa: E402
from app.core.enums import DocumentStatus  # noqa: E402
from app.models.orchestrate_request import OrchestrateRequest  # noqa: E402
from app.models.status_message import StatusMessage  # noqa: E402
from app.models.task_message import TaskMessage  # noqa: E402
from app.orchestrator.session import SessionStore  # noqa: E402
from app.orchestrator.summary import MarkdownReportGenerator  # noqa: E402
from app.repositories.document_repository import DocumentRepository  # noqa: E402
from app.services.ingestor_service import IngestorService  # noqa: E402
from app.services.s3_service import S3Service  # noqa: E402
from app.services.sqs_service import SQSService  # noqa: E402
from app.utils.app_context import AppContext  # noqa: E402
from app.utils.logger import get_logger  # noqa: E402
from app.utils.postgres import close_postgres_pool, get_postgres_pool, init_db  # noqa: E402

logger = get_logger("app.orchestrator")

_session_store = SessionStore()
_summary_generator = MarkdownReportGenerator()
_poller_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _poller_task
    if config.db.uri:
        await init_db()
        logger.info("PostgreSQL initialised")
    _poller_task = asyncio.create_task(_status_queue_poller())
    logger.info("Orchestrator started — status queue poller running")
    yield
    if _poller_task:
        _poller_task.cancel()
    await close_postgres_pool()
    logger.info("Orchestrator stopped")


app = FastAPI(title="AIA Orchestrator", version="1.0.0", lifespan=lifespan)


@app.post("/orchestrate", status_code=202)
async def orchestrate(
    request: OrchestrateRequest, background_tasks: BackgroundTasks
) -> dict:
    logger.info(
        "Orchestrate request received doc_id=%s s3_key=%s template_type=%s",
        request.document_id,
        request.s3_key,
        request.template_type,
    )
    background_tasks.add_task(
        _process_document,
        request.document_id,
        request.s3_key,
        request.template_type,
    )
    return {"status": "accepted"}


async def _process_document(doc_id: str, s3_key: str, template_type: str) -> None:
    pool = await get_postgres_pool()
    context = AppContext()
    repo = DocumentRepository(pool, context)
    s3 = S3Service()
    sqs = SQSService()
    ingestor = IngestorService()

    try:
        await repo.update_status(doc_id, DocumentStatus.PROCESSING.value)

        file_bytes = await s3.download_file(s3_key)
        file_content = ingestor.extract_text_from_docx(file_bytes)

        agent_types = config.get_agent_types(template_type)
        inline_content = (
            file_content
            if len(file_content.encode()) <= config.orchestrator.max_inline_bytes
            else None
        )
        tasks = [
            TaskMessage(
                task_id=f"{doc_id}_{agent_type}",
                document_id=doc_id,
                agent_type=agent_type,
                template_type=template_type,
                file_content=inline_content,
                s3_bucket=config.s3.bucket_name,
                s3_key=s3_key,
            )
            for agent_type in agent_types
        ]
        await asyncio.gather(*[sqs.send_task(t) for t in tasks])
        logger.info(
            "Dispatched %d task(s) for doc_id=%s template_type=%s agents=%s",
            len(tasks),
            doc_id,
            template_type,
            agent_types,
        )

        expected_task_ids = {t.task_id for t in tasks}
        session = await _session_store.create(
            doc_id, template_type, s3_key, expected_task_ids
        )

        timed_out = False
        try:
            await asyncio.wait_for(
                session.completion_event.wait(),
                timeout=float(config.orchestrator.agent_timeout_seconds),
            )
        except asyncio.TimeoutError:
            timed_out = True
            logger.warning(
                "Agent timeout reached for doc_id=%s after %ds",
                doc_id,
                config.orchestrator.agent_timeout_seconds,
            )

        expected = set(session.expected_task_ids)
        collected = dict(session.collected_results)
        await _session_store.remove(doc_id)

        _pipeline_cfg = PipelineConfig()
        by_agent_type: dict[str, Any] = {
            task_id.rsplit("_", 1)[1]: result for task_id, result in collected.items()
        }
        document_title = Path(s3_key).name

        if not timed_out:
            result_md = _summary_generator.generate(
                results=by_agent_type,
                document_title=document_title,
                section_labels=_pipeline_cfg.section_labels,
                agent_type_order=_pipeline_cfg.agent_types,
            )
            await repo.update_status(
                doc_id, DocumentStatus.COMPLETE.value, result_md=result_md
            )
            logger.info("Document completed doc_id=%s", doc_id)
        elif not collected:
            await repo.update_status(
                doc_id,
                DocumentStatus.ERROR.value,
                error_message="No agent responses received within timeout.",
            )
            logger.error("Document failed (0 responses) doc_id=%s", doc_id)
        else:
            result_md = _summary_generator.generate(
                results=by_agent_type,
                document_title=document_title,
                section_labels=_pipeline_cfg.section_labels,
                agent_type_order=_pipeline_cfg.agent_types,
            )
            missing = expected - collected.keys()
            missing_types = ", ".join(t.rsplit("_", 1)[-1] for t in missing)
            await repo.update_status(
                doc_id,
                DocumentStatus.PARTIAL_COMPLETE.value,
                result_md=result_md,
                error_message=f"Agents did not respond within timeout: {missing_types}",
            )
            logger.warning(
                "Document partially completed doc_id=%s missing=%s",
                doc_id,
                missing_types,
            )

    except Exception as exc:
        logger.exception("Document processing failed doc_id=%s: %s", doc_id, exc)
        try:
            await repo.update_status(
                doc_id,
                DocumentStatus.ERROR.value,
                error_message=str(exc),
            )
        except Exception:
            logger.exception("Failed to write ERROR status for doc_id=%s", doc_id)


async def _status_queue_poller() -> None:
    """Continuously polls aia-status for agent results and routes them to the correct session."""
    sqs = SQSService()
    queue_url = config.sqs.status_queue_url
    logger.info("Status queue poller started — polling %s", queue_url)

    while True:
        try:
            messages = await sqs.receive_messages(
                queue_url, max_messages=10, wait_seconds=20
            )
            for msg in messages:
                receipt = msg["receipt_handle"]
                try:
                    status_msg = StatusMessage.model_validate_json(msg["body"])
                    if status_msg.error:
                        agent_result = None
                    else:
                        agent_result = AgentResult.model_validate(status_msg.result)
                    all_received = await _session_store.record_result(
                        status_msg.document_id,
                        status_msg.task_id,
                        agent_result,
                    )
                    await sqs.delete_message(queue_url, receipt)
                    logger.info(
                        "Result recorded task_id=%s all_received=%s",
                        status_msg.task_id,
                        all_received,
                    )
                except Exception as exc:
                    logger.exception(
                        "Failed to process status message: %s — body=%s",
                        exc,
                        msg["body"],
                    )
        except asyncio.CancelledError:
            logger.info("Status queue poller stopped")
            return
        except Exception as exc:
            logger.exception("Status queue poller error: %s", exc)
            await asyncio.sleep(5)


def main() -> None:
    uvicorn.run(
        "app.orchestrator.main:app",
        host=config.app.host,
        port=config.orchestrator.port,
        reload=config.app.env == "development",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.critical("Fatal orchestrator error: %s", exc)
        sys.exit(1)
