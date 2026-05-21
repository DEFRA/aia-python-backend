"""Backward-compatible re-export — actual implementation in src/worker.py."""

from app.agent_service.src.worker import *  # noqa: F401, F403
from app.agent_service.src.worker import (
    NonRetriableTaskMessageError,
    dispatch,
    run_worker,
    _get_document,
    _extract_text,
    _get_db_config,
    _parse_task_message,
    _process_message,
    AGENT_REGISTRY,
    CONFIG_REGISTRY,
    MAX_CONCURRENT_TASKS,
)

# Re-export for tests that patch at the old path
from app.agent_service.src.database.questions_repo import (  # noqa: F401
    fetch_all_policy_docs_by_category,
    fetch_questions_by_policy_doc_id,
)
from app.agent_service.src.utils.llm_client import make_llm_client  # noqa: F401

