"""LLM client factory for the agent service.

Returns an ``anthropic.AsyncAnthropic``-compatible client based on the
``llm.provider`` value in ``config.yaml`` (overridable via ``LLM_PROVIDER``
env var).
"""

from __future__ import annotations

import anthropic

from app.agent_service.src.config import LLMConfig


def make_llm_client() -> anthropic.AsyncAnthropic:
    """Return an async LLM client for the configured provider."""
    config: LLMConfig = LLMConfig()
    if config.provider == "bedrock":
        return anthropic.AsyncAnthropicBedrock()  # type: ignore[return-value]
    return anthropic.AsyncAnthropic()
