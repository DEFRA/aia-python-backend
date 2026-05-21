"""LLM client factory for the evaluation pipeline.

Returns an ``anthropic.AsyncAnthropic``-compatible client based on the
``llm.provider`` value in ``config.yaml`` (overridable via ``LLM_PROVIDER``
env var).  All agents type their ``client`` argument as ``AsyncAnthropic``;
``AsyncAnthropicBedrock`` is duck-compatible so no cast is needed at call sites.

Transport-level tunables (``max_retries`` and ``timeout``) are sourced from
``LLMConfig.sdk_max_retries`` and ``LLMConfig.request_timeout_s`` respectively.
The SDK retry count defaults to ``0`` because tenacity owns retry policy in
this pipeline (see ``src/utils/retry.py``).
"""

from __future__ import annotations

import anthropic

from src.config import LLMConfig


def make_llm_client() -> anthropic.AsyncAnthropic:
    """Return an async LLM client for the configured provider.

    * ``provider="anthropic"`` → ``AsyncAnthropic()`` (reads ``ANTHROPIC_API_KEY``
      from the environment automatically).
    * ``provider="bedrock"``   → ``AsyncAnthropicBedrock()`` (uses the boto3
      credential chain; no API key required).

    The chosen client is constructed with ``max_retries`` and ``timeout``
    keyword arguments populated from ``LLMConfig``.  No literal numbers are
    embedded in this module — every value flows from ``config.yaml`` /
    environment variables via ``LLMConfig``.

    Returns:
        An ``AsyncAnthropic`` or duck-compatible ``AsyncAnthropicBedrock``
        instance ready for use by any pipeline agent.

    Raises:
        pydantic_settings.ValidationError: If ``LLM_PROVIDER`` is set to an
            unknown value.
    """
    config: LLMConfig = LLMConfig()
    if config.provider == "bedrock":
        return anthropic.AsyncAnthropicBedrock(  # type: ignore[return-value]
            max_retries=config.sdk_max_retries,
            timeout=config.request_timeout_s,
        )
    return anthropic.AsyncAnthropic(
        max_retries=config.sdk_max_retries,
        timeout=config.request_timeout_s,
    )
