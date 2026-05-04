"""Postgres reader for assessment questions.

Reads from the data_pipeline schema populated by the datapipeline Lambda.
Two-step lookup: category → policy_doc_id → questions.
"""

from __future__ import annotations

import logging

import asyncpg

from src.agents.schemas import QuestionItem
from src.utils.exceptions import UnknownCategoryError

logger: logging.Logger = logging.getLogger(__name__)

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
    dsn: str,
    category: str,
) -> tuple[str, str, str]:
    """Resolve a category to the most recently created policy document.

    Args:
        dsn: asyncpg-compatible connection string.
        category: Category name (e.g. ``"security"``, ``"technical"``).
            Case-insensitive.

    Returns:
        ``(policy_doc_id, policy_doc_url, policy_doc_filename)`` for the most recently created
        policy document in this category.

    Raises:
        UnknownCategoryError: If no policy document exists for the given category.
        asyncpg.PostgresError: On connection or query failure.
    """
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(_FETCH_POLICY_DOC_SQL, category)
    finally:
        await conn.close()

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
    dsn: str,
    category: str,
) -> list[tuple[str, str, str]]:
    """Return all policy documents for a category, ordered by creation date ascending.

    Args:
        dsn: asyncpg-compatible connection string.
        category: Category name (e.g. ``"security"``, ``"technical"``).
            Case-insensitive.

    Returns:
        List of ``(policy_doc_id, source_url, filename)`` tuples, oldest first.
        Returns an empty list when no documents exist for the category.

    Raises:
        asyncpg.PostgresError: On connection or query failure.
    """
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(_FETCH_ALL_POLICY_DOCS_SQL, category)
    finally:
        await conn.close()

    docs: list[tuple[str, str, str]] = [
        (row["policy_doc_id"], row["source_url"], row["filename"]) for row in rows
    ]
    logger.info(
        "Fetched %d policy doc(s) for category=%r",
        len(docs),
        category,
    )
    return docs


async def fetch_policy_doc_by_id(
    dsn: str,
    policy_doc_id: str,
) -> tuple[str, str, str]:
    """Fetch a specific policy document by its primary key.

    Args:
        dsn: asyncpg-compatible connection string.
        policy_doc_id: UUID of the policy document (text form).

    Returns:
        ``(policy_doc_id, source_url, filename)`` for the requested document.

    Raises:
        UnknownCategoryError: If no policy document exists for the given ID.
        asyncpg.PostgresError: On connection or query failure.
    """
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(_FETCH_POLICY_DOC_BY_ID_SQL, policy_doc_id)
    finally:
        await conn.close()

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
    dsn: str,
    policy_doc_id: str,
) -> list[QuestionItem]:
    """Fetch active questions for a policy document by its primary key.

    Args:
        dsn: asyncpg-compatible connection string.
        policy_doc_id: UUID of the policy document (text form).

    Returns:
        List of active ``QuestionItem`` instances ordered by creation date.

    Raises:
        asyncpg.PostgresError: On connection or query failure.
    """
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(_FETCH_QUESTIONS_SQL, policy_doc_id)
    finally:
        await conn.close()

    questions: list[QuestionItem] = [
        QuestionItem(id=row["id"], question=row["question_text"], reference=row["reference"])
        for row in rows
    ]
    logger.info(
        "Fetched %d question(s) for policy_doc_id=%s",
        len(questions),
        policy_doc_id,
    )
    return questions
