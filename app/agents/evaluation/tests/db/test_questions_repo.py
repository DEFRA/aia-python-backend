"""Tests for src.db.questions_repo.fetch_assessment_by_category."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from src.db.questions_repo import fetch_assessment_by_category
from src.utils.exceptions import UnknownCategoryError


def _row(question_text: str, reference: str, source_url: str) -> dict:
    return {"question_text": question_text, "reference": reference, "source_url": source_url}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_typed_questions_and_category_url() -> None:
    """Returns QuestionItem list and source URL of the most recent policy doc."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        _row("Is MFA enabled?", "C1.a", "https://example.com/security"),
        _row("Is data encrypted at rest?", "C2.b", "https://example.com/security"),
    ]

    with patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        questions, category_url = await fetch_assessment_by_category(
            "postgresql://u:p@h/db", "security"
        )

    assert len(questions) == 2
    assert questions[0].question == "Is MFA enabled?"
    assert questions[0].reference == "C1.a"
    assert questions[1].question == "Is data encrypted at rest?"
    assert questions[1].reference == "C2.b"
    assert category_url == "https://example.com/security"


@pytest.mark.asyncio
async def test_category_url_taken_from_first_row() -> None:
    """category_url comes from the first row (most recently created policy doc)."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        _row("Q1", "R1", "https://example.com/newer"),
        _row("Q2", "R2", "https://example.com/older"),
    ]

    with patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        _, category_url = await fetch_assessment_by_category("dsn", "technical")

    assert category_url == "https://example.com/newer"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raises_unknown_category_error_when_no_rows() -> None:
    """UnknownCategoryError raised when no active questions exist for the category."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    with (
        patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)),
        pytest.raises(UnknownCategoryError, match="no-such-category"),
    ):
        await fetch_assessment_by_category("dsn", "no-such-category")


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_closed_after_successful_fetch() -> None:
    """Connection is always closed after a successful fetch."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [_row("Q", "R", "https://example.com")]

    with patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        await fetch_assessment_by_category("dsn", "security")

    mock_conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connection_closed_when_fetch_raises() -> None:
    """Connection is closed even when the query raises an exception."""
    mock_conn = AsyncMock()
    mock_conn.fetch.side_effect = asyncpg.PostgresConnectionError("connection lost")

    with (
        patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)),
        pytest.raises(asyncpg.PostgresConnectionError),
    ):
        await fetch_assessment_by_category("dsn", "security")

    mock_conn.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# SQL contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sql_filters_by_category_case_insensitive_and_isactive() -> None:
    """SQL must use LOWER($1) for case-insensitive match and filter isactive = true."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [_row("Q", "R", "https://example.com")]

    with patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        await fetch_assessment_by_category("dsn", "Security")

    sql: str = mock_conn.fetch.call_args[0][0]
    passed_category: str = mock_conn.fetch.call_args[0][1]

    assert "LOWER($1)" in sql
    assert "q.isactive = true" in sql
    assert "data_pipeline.questions" in sql
    assert "data_pipeline.policy_documents" in sql
    assert "pd.category" in sql
    assert passed_category == "Security"
