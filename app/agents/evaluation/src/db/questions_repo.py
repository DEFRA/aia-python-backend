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
    SELECT policy_doc_id::text, source_url
    FROM data_pipeline.policy_documents
    WHERE LOWER(category) = LOWER($1)
    ORDER BY created_at DESC
    LIMIT 1
"""

_FETCH_QUESTIONS_SQL = """
    SELECT question_text, reference
    FROM data_pipeline.questions
    WHERE policy_doc_id = $1::uuid
      AND isactive = true
    ORDER BY created_at ASC
"""


async def fetch_policy_doc_by_category(
    dsn: str,
    category: str,
) -> tuple[str, str]:
    """Resolve a category to the most recently created policy document.

    Args:
        dsn: asyncpg-compatible connection string.
        category: Category name (e.g. ``"security"``, ``"technical"``).
            Case-insensitive.

    Returns:
        ``(policy_doc_id, policy_doc_url)`` for the most recently created
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
    logger.info(
        "Resolved category=%r to policy_doc_id=%s policy_doc_url=%s",
        category,
        policy_doc_id,
        policy_doc_url,
    )
    return policy_doc_id, policy_doc_url


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
        QuestionItem(question=row["question_text"], reference=row["reference"]) for row in rows
    ]
    logger.info(
        "Fetched %d question(s) for policy_doc_id=%s",
        len(questions),
        policy_doc_id,
    )
    return questions
