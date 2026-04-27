"""PostgreSQL repository for fetching checklist questions by category."""

import logging

import asyncpg

logger: logging.Logger = logging.getLogger(__name__)

# Expected table schema:
#
#   CREATE TABLE checklist_questions (
#       id          SERIAL PRIMARY KEY,
#       category    VARCHAR(100) NOT NULL,
#       question    TEXT         NOT NULL
#   );


async def fetch_questions_by_category(
    dsn: str,
    category: str,
) -> list[str]:
    """Fetch all checklist questions for a given category from PostgreSQL.

    Args:
        dsn: asyncpg-compatible connection string, e.g.
             "postgresql://user:password@host:5432/dbname"
        category: The category name to filter by (case-insensitive),
                  e.g. "Security".

    Returns:
        An ordered list of question strings for the requested category.

    Raises:
        asyncpg.PostgresError: If the database query fails.
        ValueError: If no questions are found for the given category.
    """
    conn: asyncpg.Connection = await asyncpg.connect(dsn)
    try:
        rows: list[asyncpg.Record] = await conn.fetch(
            """
            SELECT question
            FROM   checklist_questions
            WHERE  LOWER(category) = LOWER($1)
            ORDER  BY id
            """,
            category,
        )
    finally:
        await conn.close()

    questions: list[str] = [row["question"] for row in rows]

    if not questions:
        raise ValueError(
            f"No questions found for category '{category}'. "
            "Check the category name and that the table is populated."
        )

    logger.info(
        "Loaded %d questions for category '%s' from database.",
        len(questions),
        category,
    )
    return questions
