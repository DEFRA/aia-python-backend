"""Tenacity-driven retry policy for LLM agent calls.

The pipeline owns retry behaviour at the agent level: each call to the
Anthropic API is wrapped by ``agent_retry()`` so a single transient blip
(connection reset, 429 rate limit, 5xx, occasional malformed JSON) does
not waste the work of upstream pipeline stages.

* The classification predicate (which exceptions are transient vs terminal)
  lives in ``_is_transient`` and is hard-coded — it has no tunables.
* The numeric backoff parameters (``max_attempts``, ``initial_wait_s``,
  ``max_wait_s``, ``jitter_s``) come from :class:`src.config.RetryConfig`,
  which is in turn populated from ``config.yaml`` / ``RETRY_*`` env vars.
* The Anthropic SDK's silent built-in retries are disabled
  (``LLMConfig.sdk_max_retries = 0``) so retry logic lives in exactly one
  place.

Transient (will be retried):
* ``anthropic.APIConnectionError``
* ``anthropic.APITimeoutError``
* ``anthropic.RateLimitError``
* ``anthropic.APIStatusError`` with ``500 <= status_code < 600``
* ``json.JSONDecodeError``

Terminal (re-raised on the first attempt):
* ``anthropic.APIStatusError`` with any non-5xx ``status_code`` (4xx, 3xx)
* ``pydantic.ValidationError``
* ``KeyError``
* Any other exception type
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from app.agents.evaluation.src.config import RetryConfig
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger: logging.Logger = logging.getLogger(__name__)

_HTTP_SERVER_ERROR_FLOOR: int = 500
_HTTP_SERVER_ERROR_CEILING: int = 600

_TRANSIENT_EXC_TYPES: tuple[type[BaseException], ...] = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    json.JSONDecodeError,
)


def _is_transient(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` represents a transient failure worth retrying.

    Args:
        exc: The exception raised by the wrapped call.

    Returns:
        ``True`` for connection / timeout / rate-limit errors, 5xx HTTP
        responses, and JSON decode failures.  ``False`` for everything
        else, including 4xx HTTP responses, ``pydantic.ValidationError``,
        and ``KeyError``.
    """
    if isinstance(exc, _TRANSIENT_EXC_TYPES):
        return True
    if isinstance(exc, APIStatusError):
        status: int | None = getattr(exc, "status_code", None)
        if status is None:
            return False
        return _HTTP_SERVER_ERROR_FLOOR <= status < _HTTP_SERVER_ERROR_CEILING
    return False


_FuncT = TypeVar("_FuncT", bound=Callable[..., Awaitable[Any]])


def agent_retry(
    config: RetryConfig | None = None,
) -> Callable[[_FuncT], _FuncT]:
    """Build a tenacity retry decorator using the supplied ``RetryConfig``.

    The decorator retries the wrapped coroutine while ``_is_transient``
    classifies the raised exception as transient, sleeping with
    exponential backoff bounded by ``config.max_wait_s`` and jittered by
    ``config.jitter_s``.  Terminal exceptions are re-raised immediately.

    The TypeVar bound preserves the wrapped function's full signature so
    structural protocols (e.g. ``SpecialistAgent`` in ``src/handlers/agent.py``)
    still match a decorated method.

    Args:
        config: A populated :class:`RetryConfig`.  When ``None`` the
            default ``RetryConfig()`` is used (which itself reads
            ``config.yaml`` / ``RETRY_*`` env vars).  Pass a custom
            instance from tests to disable waits or shorten attempts.

    Returns:
        A decorator suitable for application to async agent methods.
    """
    cfg: RetryConfig = config if config is not None else RetryConfig()

    def _decorator(func: _FuncT) -> _FuncT:
        wrapped: Any = retry(
            stop=stop_after_attempt(cfg.max_attempts),
            wait=wait_exponential_jitter(
                initial=cfg.initial_wait_s,
                max=cfg.max_wait_s,
                jitter=cfg.jitter_s,
            ),
            retry=retry_if_exception(_is_transient),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )(func)
        return wrapped  # type: ignore[no-any-return]

    return _decorator
