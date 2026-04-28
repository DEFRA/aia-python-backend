"""Postgres reader stub for per-category assessment input.

The Postgres schema for the assessment input table is TBC. Until the layout is
finalised, callers should use ``src.db.assessment_loader.load_assessment_from_file``;
this module preserves the agreed async signature so the swap-over at
``handlers/extract_sections.py`` and ``main.py`` is a one-line change.

When implemented, the function is expected to read from a categories table
(carrying the per-category SharePoint reference URL) joined to a questions
table (one row per checklist question with its authoritative reference text)
and return ``(list[QuestionItem], category_url)`` -- exactly the shape produced
by the file-based loader today.
"""

from __future__ import annotations

import logging

from src.agents.schemas import QuestionItem

logger: logging.Logger = logging.getLogger(__name__)


async def fetch_assessment_by_category(
    dsn: str,
    category: str,
) -> tuple[list[QuestionItem], str]:
    """Fetch ``(questions, category_url)`` for a category from Postgres.

    The Postgres schema is TBC. Until the table layout is finalised, callers
    should use ``src.db.assessment_loader.load_assessment_from_file``. This
    stub preserves the agreed signature so the swap-over is a one-line
    change at the call sites in ``extract_sections.py`` and ``main.py``.

    Args:
        dsn: asyncpg-compatible connection string.
        category: The category name to filter by (e.g. ``"Security"``).

    Returns:
        A ``(questions, category_url)`` tuple, identical in shape to
        ``load_assessment_from_file``.

    Raises:
        NotImplementedError: Always, until the Postgres schema is in place.
    """
    raise NotImplementedError(
        "Postgres assessment schema is TBC; "
        "use load_assessment_from_file from src.db.assessment_loader."
    )
