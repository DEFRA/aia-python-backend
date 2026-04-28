"""Tests for the Postgres assessment reader stub."""

from __future__ import annotations

import pytest

from src.db.questions_repo import fetch_assessment_by_category


@pytest.mark.asyncio
async def test_fetch_assessment_by_category_raises_not_implemented() -> None:
    """The stub must raise NotImplementedError until the Postgres schema is finalised."""
    with pytest.raises(NotImplementedError):
        await fetch_assessment_by_category(
            dsn="postgresql://u:p@h:5432/db",
            category="Security",
        )
