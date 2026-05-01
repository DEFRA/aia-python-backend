"""Postgres reader for per-category assessment questions.

Reads from the data_pipeline schema populated by the datapipeline Lambda.
Returns ``(list[QuestionItem], category_url)``.
"""

from __future__ import annotations

import logging

import asyncpg

from src.agents.schemas import QuestionItem
from src.utils.exceptions import UnknownCategoryError

logger: logging.Logger = logging.getLogger(__name__)

_FETCH_QUESTIONS_SQL = """
    SELECT q.question_text, q.reference, pd.source_url
    FROM data_pipeline.questions q
    JOIN data_pipeline.policy_documents pd ON q.policy_doc_id = pd.policy_doc_id
    WHERE LOWER(pd.category) = LOWER($1)
      AND q.isactive = true
    ORDER BY pd.created_at DESC, q.created_at ASC
"""


async def fetch_assessment_by_category(
    dsn: str,
    category: str,
) -> tuple[list[QuestionItem], str]:
    """Fetch active questions for a category from data_pipeline.questions.

    Args:
        dsn: asyncpg-compatible connection string.
        category: Category name (e.g. ``"security"``, ``"technical"``).
            Case-insensitive — matched with ``LOWER()`` in SQL.

    Returns:
        ``(questions, category_url)`` where ``questions`` is the list of active
        ``QuestionItem`` instances and ``category_url`` is the ``source_url`` of
        the most recently created policy document contributing questions in this
        category (used as ``Reference.url`` in every assessment row).

    Raises:
        UnknownCategoryError: If no active questions exist for the given category.
        asyncpg.PostgresError: On connection or query failure.
    """
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(_FETCH_QUESTIONS_SQL, category)
    finally:
        await conn.close()

    if not rows:
        raise UnknownCategoryError(
            f"No active questions found for category: {category!r}"
        )

    questions: list[QuestionItem] = [
        QuestionItem(question=row["question_text"], reference=row["reference"])
        for row in rows
    ]
    category_url: str = rows[0]["source_url"]
    logger.info(
        "Fetched %d question(s) for category=%r category_url=%s",
        len(questions),
        category,
        category_url,
    )
    return questions, category_url
