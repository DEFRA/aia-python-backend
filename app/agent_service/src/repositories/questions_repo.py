"""Postgres reader for assessment questions.

Reads from the data_pipeline schema populated by the datapipeline Lambda.
Two-step lookup: category → policy_doc_id → questions.
"""

from __future__ import annotations

import logging

from app.agent_service.src.models.schemas import QuestionItem
from app.agent_service.src.utils.exceptions import UnknownCategoryError
from app.agent_service.src.db_pool import get_pool

logger: logging.Logger = logging.getLogger(__name__)


def _resolve_category_arg(first: str, second: str | None) -> str:
    """Support both legacy 2-arg (dsn, category) and current 1-arg (category) callers."""
    if second is None:
        return first
    logger.warning(
        "Deprecated 2-arg repository call detected; dsn arg is ignored (pool is used instead)"
    )
    return second


_FETCH_POLICY_DOC_SQL = """
    SELECT policy_doc_id::text, source_url, filename
    FROM data_pipeline.policy_documents
    WHERE LOWER(category) = LOWER($1)
    ORDER BY created_at DESC
    LIMIT 1
"""

_FETCH_ALL_POLICY_DOCS_SQL = """
    SELECT policy_doc_id::text, source_url, filename
    FROM data_pipeline.policy_documents
    WHERE LOWER(category) = LOWER($1)
    ORDER BY created_at ASC
"""

_FETCH_POLICY_DOC_BY_ID_SQL = """
    SELECT policy_doc_id::text, source_url, filename
    FROM data_pipeline.policy_documents
    WHERE policy_doc_id = $1::uuid
"""

_FETCH_QUESTIONS_SQL = """
    SELECT id::text, question_text, reference
    FROM data_pipeline.questions
    WHERE policy_doc_id = $1::uuid
      AND isactive = true
    ORDER BY created_at ASC
"""


async def fetch_policy_doc_by_category(
    category: str,
) -> tuple[str, str, str]:
    """Resolve a category to the most recently created policy document.

    Raises:
        UnknownCategoryError: If no policy document exists for the given category.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_FETCH_POLICY_DOC_SQL, category)

    if row is None:
        raise UnknownCategoryError(f"No policy document found for category: {category!r}")

    policy_doc_id: str = row["policy_doc_id"]
    policy_doc_url: str = row["source_url"]
    policy_doc_filename: str = row["filename"]
    logger.info(
        "Resolved category=%r to policy_doc_id=%s policy_doc_url=%s",
        category,
        policy_doc_id,
        policy_doc_url,
    )
    return policy_doc_id, policy_doc_url, policy_doc_filename


async def fetch_all_policy_docs_by_category(
    category_or_dsn: str,
    category: str | None = None,
) -> list[tuple[str, str, str]]:
    """Return all policy documents for a category, ordered by creation date ascending."""
    resolved = _resolve_category_arg(category_or_dsn, category)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_FETCH_ALL_POLICY_DOCS_SQL, resolved)

    docs: list[tuple[str, str, str]] = [
        (row["policy_doc_id"], row["source_url"], row["filename"]) for row in rows
    ]
    logger.info(
        "Fetched %d policy doc(s) for category=%r",
        len(docs),
        resolved,
    )
    return docs


async def fetch_policy_doc_by_id(
    policy_doc_id: str,
) -> tuple[str, str, str]:
    """Fetch a specific policy document by its primary key."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_FETCH_POLICY_DOC_BY_ID_SQL, policy_doc_id)

    if row is None:
        raise UnknownCategoryError(f"No policy document found for policy_doc_id: {policy_doc_id!r}")

    doc_id: str = row["policy_doc_id"]
    doc_url: str = row["source_url"]
    doc_filename: str = row["filename"]
    logger.info(
        "Fetched policy_doc_id=%s source_url=%s",
        doc_id,
        doc_url,
    )
    return doc_id, doc_url, doc_filename


async def fetch_questions_by_policy_doc_id(
    policy_doc_id_or_dsn: str,
    policy_doc_id: str | None = None,
) -> list[QuestionItem]:
    """Fetch all active questions for a policy document, ordered by creation date."""
    resolved = _resolve_category_arg(policy_doc_id_or_dsn, policy_doc_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_FETCH_QUESTIONS_SQL, resolved)

    questions: list[QuestionItem] = [
        QuestionItem(id=row["id"], question=row["question_text"], reference=row["reference"])
        for row in rows
    ]
    logger.info(
        "Fetched %d question(s) for policy_doc_id=%s",
        len(questions),
        resolved,
    )
    return questions
