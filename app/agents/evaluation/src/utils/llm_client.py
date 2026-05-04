"""LLM client factory for the evaluation pipeline.

Returns an ``anthropic.AsyncAnthropic``-compatible client based on the
``llm.provider`` value in ``config.yaml`` (overridable via ``LLM_PROVIDER``
env var).  All agents type their ``client`` argument as ``AsyncAnthropic``;
``AsyncAnthropicBedrock`` is duck-compatible so no cast is needed at call sites.
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

    Returns:
        An ``AsyncAnthropic`` or duck-compatible ``AsyncAnthropicBedrock``
        instance ready for use by any pipeline agent.

    Raises:
        pydantic_settings.ValidationError: If ``LLM_PROVIDER`` is set to an
            unknown value.
    """
    config: LLMConfig = LLMConfig()
    if config.provider == "bedrock":
        return anthropic.AsyncAnthropicBedrock()  # type: ignore[return-value]
    return anthropic.AsyncAnthropic()
