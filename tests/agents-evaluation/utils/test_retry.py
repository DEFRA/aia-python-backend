"""Tests for ``src.utils.retry`` — predicate and decorator factory."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from src.config import RetryConfig
from src.utils.retry import _is_transient, agent_retry

# ---------------------------------------------------------------------------
# Helpers for constructing anthropic exceptions
# ---------------------------------------------------------------------------


def _make_request() -> httpx.Request:
    """Build a minimal httpx.Request suitable for SDK exception construction."""
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _make_status_error(status_code: int) -> APIStatusError:
    """Build an APIStatusError with the given HTTP status."""
    response: httpx.Response = httpx.Response(
        status_code=status_code, request=_make_request()
    )
    return APIStatusError(message=f"status {status_code}", response=response, body=None)


def _make_rate_limit_error() -> RateLimitError:
    """Build a RateLimitError with status 429."""
    response: httpx.Response = httpx.Response(status_code=429, request=_make_request())
    return RateLimitError(message="rate limited", response=response, body=None)


# ---------------------------------------------------------------------------
# _is_transient — transient classifications
# ---------------------------------------------------------------------------


def test_is_transient_classifies_anthropic_connection_error_as_transient() -> None:
    """APIConnectionError is transient."""
    exc: APIConnectionError = APIConnectionError(request=_make_request())
    assert _is_transient(exc) is True


def test_is_transient_classifies_anthropic_timeout_error_as_transient() -> None:
    """APITimeoutError is transient."""
    exc: APITimeoutError = APITimeoutError(request=_make_request())
    assert _is_transient(exc) is True


def test_is_transient_classifies_rate_limit_error_as_transient() -> None:
    """RateLimitError is transient."""
    assert _is_transient(_make_rate_limit_error()) is True


def test_is_transient_classifies_json_decode_error_as_transient() -> None:
    """json.JSONDecodeError is transient."""
    try:
        json.loads("not json")
    except json.JSONDecodeError as exc:
        assert _is_transient(exc) is True
    else:  # pragma: no cover - guaranteed to raise
        pytest.fail("Expected JSONDecodeError")


def test_is_transient_classifies_5xx_status_error_as_transient() -> None:
    """APIStatusError with 5xx status is transient."""
    assert _is_transient(_make_status_error(503)) is True
    assert _is_transient(_make_status_error(500)) is True


# ---------------------------------------------------------------------------
# _is_transient — terminal classifications
# ---------------------------------------------------------------------------


def test_is_transient_classifies_4xx_status_error_as_terminal() -> None:
    """APIStatusError with 4xx status is terminal."""
    assert _is_transient(_make_status_error(400)) is False
    assert _is_transient(_make_status_error(401)) is False


def test_is_transient_classifies_validation_error_as_terminal() -> None:
    """pydantic.ValidationError is terminal."""

    class _Model(BaseModel):
        x: int

    try:
        _Model.model_validate({"x": "not-an-int"})
    except ValidationError as exc:
        assert _is_transient(exc) is False
    else:  # pragma: no cover - guaranteed to raise
        pytest.fail("Expected ValidationError")


def test_is_transient_classifies_key_error_as_terminal() -> None:
    """KeyError is terminal."""
    assert _is_transient(KeyError("missing")) is False


def test_is_transient_classifies_value_error_as_terminal() -> None:
    """Plain ValueError is terminal."""
    assert _is_transient(ValueError("bad")) is False


def test_is_transient_classifies_runtime_error_as_terminal() -> None:
    """RuntimeError is terminal."""
    assert _is_transient(RuntimeError("boom")) is False


# ---------------------------------------------------------------------------
# agent_retry decorator — behaviour
# ---------------------------------------------------------------------------


def _zero_wait_config() -> RetryConfig:
    """Build a RetryConfig with zero waits to keep tests fast."""
    return RetryConfig(
        max_attempts=3,
        initial_wait_s=0.0,
        max_wait_s=0.0,
        jitter_s=0.0,
    )


@pytest.mark.asyncio
async def test_agent_retry_factory_uses_supplied_config() -> None:
    """agent_retry(config=...) honours max_attempts and retries on transient errors."""
    cfg: RetryConfig = RetryConfig(
        max_attempts=2, initial_wait_s=0.0, max_wait_s=0.0, jitter_s=0.0
    )

    inner: AsyncMock = AsyncMock(
        side_effect=[APIConnectionError(request=_make_request()), "ok"],
    )

    @agent_retry(config=cfg)
    async def call() -> str:
        return await inner()

    result: str = await call()

    assert result == "ok"
    assert inner.await_count == 2


@pytest.mark.asyncio
async def test_agent_retry_does_not_retry_terminal_errors() -> None:
    """A terminal error (4xx) must not trigger any retry."""
    cfg: RetryConfig = _zero_wait_config()
    inner: AsyncMock = AsyncMock(side_effect=_make_status_error(400))

    @agent_retry(config=cfg)
    async def call() -> str:
        return await inner()

    with pytest.raises(APIStatusError):
        await call()

    assert inner.await_count == 1


@pytest.mark.asyncio
async def test_agent_retry_logs_warning_before_each_sleep(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each retry should emit a WARNING log record from the retry module."""
    cfg: RetryConfig = _zero_wait_config()
    inner: AsyncMock = AsyncMock(
        side_effect=[
            APIConnectionError(request=_make_request()),
            APIConnectionError(request=_make_request()),
            "ok",
        ],
    )

    @agent_retry(config=cfg)
    async def call() -> str:
        return await inner()

    with caplog.at_level(logging.WARNING, logger="src.utils.retry"):
        result: str = await call()

    assert result == "ok"
    warning_records: list[logging.LogRecord] = [
        rec for rec in caplog.records if rec.levelno == logging.WARNING
    ]
    assert warning_records, "Expected at least one WARNING from src.utils.retry"


@pytest.mark.asyncio
async def test_agent_retry_default_config_runs() -> None:
    """agent_retry() with no config should still wrap the function callable."""

    @agent_retry()
    async def call() -> str:
        return "ok"

    assert await call() == "ok"
