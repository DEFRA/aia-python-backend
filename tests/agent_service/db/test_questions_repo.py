"""Tests for src.db.questions_repo — two-step questions lookup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from app.agent_service.src.repositories.questions_repo import (
    fetch_policy_doc_by_category,
    fetch_questions_by_policy_doc_id,
)
from app.agent_service.src.utils.exceptions import UnknownCategoryError

# ---------------------------------------------------------------------------
# fetch_policy_doc_by_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_policy_doc_returns_id_url_and_filename() -> None:
    """Returns (policy_doc_id, policy_doc_url, policy_doc_filename) for a matching category."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "policy_doc_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "source_url": "https://example.com/security",
        "filename": "security_policy.pdf",
    }

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with patch(
        "app.agent_service.src.repositories.questions_repo.get_pool",
        return_value=mock_pool,
    ):
        result = await fetch_policy_doc_by_category("security")
    policy_doc_id, policy_doc_url, policy_doc_filename = result

    assert policy_doc_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert policy_doc_url == "https://example.com/security"
    assert policy_doc_filename == "security_policy.pdf"


@pytest.mark.asyncio
async def test_fetch_policy_doc_raises_unknown_category_when_no_row() -> None:
    """UnknownCategoryError raised when no policy document exists for the category."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = None

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with (
        patch(
            "app.agent_service.src.repositories.questions_repo.get_pool",
            return_value=mock_pool,
        ),
        pytest.raises(UnknownCategoryError, match="no-such-category"),
    ):
        await fetch_policy_doc_by_category("no-such-category")


@pytest.mark.asyncio
async def test_fetch_policy_doc_uses_pool_acquire_context() -> None:
    """Connection is acquired and released via pool context manager."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "policy_doc_id": "some-uuid",
        "source_url": "https://example.com",
        "filename": "doc.pdf",
    }

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with patch(
        "app.agent_service.src.repositories.questions_repo.get_pool",
        return_value=mock_pool,
    ):
        await fetch_policy_doc_by_category("security")

    mock_pool.acquire.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_policy_doc_connection_released_on_error() -> None:
    """Connection is released even when the query raises."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.side_effect = asyncpg.PostgresConnectionError("lost")

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with (
        patch(
            "app.agent_service.src.repositories.questions_repo.get_pool",
            return_value=mock_pool,
        ),
        pytest.raises(asyncpg.PostgresConnectionError),
    ):
        await fetch_policy_doc_by_category("security")

    mock_pool.acquire.assert_called_once()


# ---------------------------------------------------------------------------
# fetch_questions_by_policy_doc_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_questions_returns_question_items() -> None:
    """Returns a list of QuestionItem instances with id populated."""
    from app.agent_service.src.models.schemas import QuestionItem

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {
            "id": "aaaaaaaa-0000-0000-0000-000000000001",
            "question_text": "Is MFA enabled?",
            "reference": "C1.a",
        },
        {
            "id": "aaaaaaaa-0000-0000-0000-000000000002",
            "question_text": "Is data encrypted at rest?",
            "reference": "C2.b",
        },
    ]

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with patch(
        "app.agent_service.src.repositories.questions_repo.get_pool",
        return_value=mock_pool,
    ):
        questions = await fetch_questions_by_policy_doc_id(
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )

    assert len(questions) == 2
    assert all(isinstance(q, QuestionItem) for q in questions)
    assert questions[0].id == "aaaaaaaa-0000-0000-0000-000000000001"
    assert questions[0].question == "Is MFA enabled?"
    assert questions[0].reference == "C1.a"
    assert questions[1].id == "aaaaaaaa-0000-0000-0000-000000000002"


@pytest.mark.asyncio
async def test_fetch_questions_returns_empty_list_when_no_rows() -> None:
    """Returns an empty list when there are no active questions for the document."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with patch(
        "app.agent_service.src.repositories.questions_repo.get_pool",
        return_value=mock_pool,
    ):
        questions = await fetch_questions_by_policy_doc_id("some-uuid")

    assert questions == []


@pytest.mark.asyncio
async def test_fetch_questions_uses_pool_acquire_context() -> None:
    """Connection is acquired and released via pool context manager."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with patch(
        "app.agent_service.src.repositories.questions_repo.get_pool",
        return_value=mock_pool,
    ):
        await fetch_questions_by_policy_doc_id("some-uuid")

    mock_pool.acquire.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_questions_connection_released_on_error() -> None:
    """Connection is released even when the query raises."""
    mock_conn = AsyncMock()
    mock_conn.fetch.side_effect = asyncpg.PostgresConnectionError("lost")

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with (
        patch(
            "app.agent_service.src.repositories.questions_repo.get_pool",
            return_value=mock_pool,
        ),
        pytest.raises(asyncpg.PostgresConnectionError),
    ):
        await fetch_questions_by_policy_doc_id("some-uuid")

    mock_pool.acquire.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_questions_sql_uses_policy_doc_id() -> None:
    """SQL targets questions.policy_doc_id, not category."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with patch(
        "app.agent_service.src.repositories.questions_repo.get_pool",
        return_value=mock_pool,
    ):
        await fetch_questions_by_policy_doc_id("some-uuid")

    sql, param = mock_conn.fetch.call_args[0]
    assert "policy_doc_id" in sql
    assert param == "some-uuid"
