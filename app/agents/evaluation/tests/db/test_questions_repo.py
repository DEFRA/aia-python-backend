"""Tests for src.db.questions_repo — two-step questions lookup."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from src.db.questions_repo import fetch_policy_doc_by_category, fetch_questions_by_policy_doc_id
from src.utils.exceptions import UnknownCategoryError

# ---------------------------------------------------------------------------
# fetch_policy_doc_by_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_policy_doc_returns_id_and_url() -> None:
    """Returns (policy_doc_id, policy_doc_url) for a matching category."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "policy_doc_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "source_url": "https://example.com/security",
    }

    with patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        policy_doc_id, policy_doc_url = await fetch_policy_doc_by_category("dsn", "security")

    assert policy_doc_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert policy_doc_url == "https://example.com/security"


@pytest.mark.asyncio
async def test_fetch_policy_doc_raises_unknown_category_when_no_row() -> None:
    """UnknownCategoryError raised when no policy document exists for the category."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = None

    with (
        patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)),
        pytest.raises(UnknownCategoryError, match="no-such-category"),
    ):
        await fetch_policy_doc_by_category("dsn", "no-such-category")


@pytest.mark.asyncio
async def test_fetch_policy_doc_connection_closed_on_success() -> None:
    """Connection is closed after a successful fetch."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "policy_doc_id": "some-uuid",
        "source_url": "https://example.com",
    }

    with patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        await fetch_policy_doc_by_category("dsn", "security")

    mock_conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_policy_doc_connection_closed_on_error() -> None:
    """Connection is closed even when the query raises."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.side_effect = asyncpg.PostgresConnectionError("lost")

    with (
        patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)),
        pytest.raises(asyncpg.PostgresConnectionError),
    ):
        await fetch_policy_doc_by_category("dsn", "security")

    mock_conn.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# fetch_questions_by_policy_doc_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_questions_returns_question_items() -> None:
    """Returns a list of QuestionItem instances."""
    from src.agents.schemas import QuestionItem

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"question_text": "Is MFA enabled?", "reference": "C1.a"},
        {"question_text": "Is data encrypted at rest?", "reference": "C2.b"},
    ]

    with patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        questions = await fetch_questions_by_policy_doc_id(
            "dsn", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )

    assert len(questions) == 2
    assert all(isinstance(q, QuestionItem) for q in questions)
    assert questions[0].question == "Is MFA enabled?"
    assert questions[0].reference == "C1.a"


@pytest.mark.asyncio
async def test_fetch_questions_returns_empty_list_when_no_rows() -> None:
    """Returns an empty list when there are no active questions for the document."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    with patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        questions = await fetch_questions_by_policy_doc_id("dsn", "some-uuid")

    assert questions == []


@pytest.mark.asyncio
async def test_fetch_questions_connection_closed_on_success() -> None:
    """Connection is closed after a successful fetch."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    with patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        await fetch_questions_by_policy_doc_id("dsn", "some-uuid")

    mock_conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_questions_connection_closed_on_error() -> None:
    """Connection is closed even when the query raises."""
    mock_conn = AsyncMock()
    mock_conn.fetch.side_effect = asyncpg.PostgresConnectionError("lost")

    with (
        patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)),
        pytest.raises(asyncpg.PostgresConnectionError),
    ):
        await fetch_questions_by_policy_doc_id("dsn", "some-uuid")

    mock_conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_questions_sql_uses_policy_doc_id() -> None:
    """SQL targets questions.policy_doc_id, not category."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    with patch("src.db.questions_repo.asyncpg.connect", AsyncMock(return_value=mock_conn)):
        await fetch_questions_by_policy_doc_id("dsn", "some-uuid")

    sql, param = mock_conn.fetch.call_args[0]
    assert "policy_doc_id" in sql
    assert param == "some-uuid"
