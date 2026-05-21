from unittest.mock import AsyncMock

import pytest

from app.orchestrator.src.schemas.status_message import StatusMessage
from app.orchestrator.src.main import _persist_status_tokens


@pytest.mark.asyncio
async def test_persist_status_tokens_skips_when_repo_missing():
    status_msg = StatusMessage(
        task_id="doc1_security",
        document_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_type="security",
        model_id="claude-3-5-sonnet-20241022",
        result={},
        input_tokens=100,
        output_tokens=50,
    )

    await _persist_status_tokens(status_msg, None)


@pytest.mark.asyncio
async def test_persist_status_tokens_skips_when_tokens_missing():
    status_msg = StatusMessage(
        task_id="doc1_security",
        document_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_type="security",
        result={},
        input_tokens=None,
        output_tokens=None,
    )
    repo = AsyncMock()

    await _persist_status_tokens(status_msg, repo)

    repo.upsert_cost_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_status_tokens_upserts_usage():
    status_msg = StatusMessage(
        task_id="doc1_security",
        document_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_type="security",
        model_id="claude-3-5-sonnet-20241022",
        result={},
        input_tokens=1200,
        output_tokens=480,
    )
    repo = AsyncMock()

    await _persist_status_tokens(status_msg, repo)

    repo.upsert_cost_usage.assert_awaited_once_with(
        doc_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_name="security",
        input_tokens=1200,
        output_tokens=480,
        total_cost_usd=0.0108,
    )


@pytest.mark.asyncio
async def test_persist_status_tokens_persists_partial_payload():
    status_msg = StatusMessage(
        task_id="doc1_security",
        document_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_type="security",
        model_id="claude-3-5-sonnet-20241022",
        result={},
        input_tokens=99,
        output_tokens=None,
    )
    repo = AsyncMock()

    await _persist_status_tokens(status_msg, repo)

    repo.upsert_cost_usage.assert_awaited_once_with(
        doc_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_name="security",
        input_tokens=99,
        output_tokens=0,
        total_cost_usd=0.000297,
    )


@pytest.mark.asyncio
async def test_persist_status_tokens_clamps_negative_values_to_zero():
    status_msg = StatusMessage.model_construct(
        task_id="doc1_security",
        document_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_type="security",
        model_id="claude-3-5-sonnet-20241022",
        result={},
        input_tokens=-3,
        output_tokens=-9,
    )
    repo = AsyncMock()

    await _persist_status_tokens(status_msg, repo)

    repo.upsert_cost_usage.assert_awaited_once_with(
        doc_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_name="security",
        input_tokens=0,
        output_tokens=0,
        total_cost_usd=0.0,
    )


@pytest.mark.asyncio
async def test_persist_status_tokens_does_not_raise_on_repo_error():
    status_msg = StatusMessage(
        task_id="doc1_security",
        document_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_type="security",
        model_id="unknown-model",
        result={},
        input_tokens=100,
        output_tokens=50,
    )
    repo = AsyncMock()
    repo.upsert_cost_usage.side_effect = RuntimeError("db down")

    await _persist_status_tokens(status_msg, repo)

    repo.upsert_cost_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_status_tokens_defaults_cost_to_zero_when_model_missing():
    status_msg = StatusMessage(
        task_id="doc1_security",
        document_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_type="security",
        model_id=None,
        result={},
        input_tokens=100,
        output_tokens=50,
    )
    repo = AsyncMock()

    await _persist_status_tokens(status_msg, repo)

    repo.upsert_cost_usage.assert_awaited_once_with(
        doc_id="aaaaaaaa-0000-0000-0000-000000000001",
        agent_name="security",
        input_tokens=100,
        output_tokens=50,
        total_cost_usd=0.0,
    )
