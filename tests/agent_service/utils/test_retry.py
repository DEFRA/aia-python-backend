"""Tests for ``src.utils.retry`` — async retry utility."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.agent_service.src.utils.retry import retry_async


@pytest.mark.asyncio
async def test_retry_async_succeeds_on_first_attempt() -> None:
    """retry_async passes through the return value on immediate success."""
    fn: AsyncMock = AsyncMock(return_value="ok")
    result = await retry_async(fn, max_attempts=3)
    assert result == "ok"
    assert fn.await_count == 1


@pytest.mark.asyncio
async def test_retry_async_retries_on_failure_then_succeeds() -> None:
    """retry_async retries and returns the value on a later success."""
    fn: AsyncMock = AsyncMock(side_effect=[ValueError("fail"), "ok"])

    with pytest.raises(ValueError):
        # max_attempts=1 should NOT retry
        fn2: AsyncMock = AsyncMock(side_effect=ValueError("fail"))
        await retry_async(fn2, max_attempts=1, base_delay=0.0)

    fn.reset_mock()
    fn.side_effect = [ValueError("fail"), "ok"]
    result = await retry_async(fn, max_attempts=2, base_delay=0.0)
    assert result == "ok"
    assert fn.await_count == 2


@pytest.mark.asyncio
async def test_retry_async_raises_after_max_attempts() -> None:
    """retry_async re-raises the last exception after exhausting all attempts."""
    fn: AsyncMock = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await retry_async(fn, max_attempts=3, base_delay=0.0)
    assert fn.await_count == 3


@pytest.mark.asyncio
async def test_retry_async_default_max_attempts() -> None:
    """Default max_attempts is 3."""
    fn: AsyncMock = AsyncMock(side_effect=ValueError("err"))
    with pytest.raises(ValueError):
        await retry_async(fn, base_delay=0.0)
    assert fn.await_count == 3


@pytest.mark.asyncio
async def test_retry_async_logs_warning_before_each_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each retry should emit a WARNING log record from the retry module."""
    fn: AsyncMock = AsyncMock(
        side_effect=[
            ValueError("first"),
            ValueError("second"),
            "ok",
        ]
    )

    with caplog.at_level(logging.WARNING, logger="app.agent_service.src.utils.retry"):
        result = await retry_async(fn, max_attempts=3, base_delay=0.0)

    assert result == "ok"
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 2
