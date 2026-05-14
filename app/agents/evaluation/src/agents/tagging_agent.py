"""Tagging agent -- applies security/governance tags to document chunks."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import anthropic
from anthropic.types import Message, TextBlock

from src.agents.prompts.tagging import SYSTEM_PROMPT
from src.agents.schemas import TaggedChunk
from src.config import TaggingAgentConfig
from src.utils.helpers import strip_code_fences
from src.utils.retry import agent_retry

logger: logging.Logger = logging.getLogger(__name__)


class TaggingAgent:
    """Tags document chunks with security taxonomy labels.

    Processes chunks in batches to stay within context limits.
    Uses temperature=0.0 for deterministic output per project convention.
    """

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        config: TaggingAgentConfig,
    ) -> None:
        """Initialise the tagging agent.

        Args:
            client: Anthropic async client (injected for testability).
            config: ``TaggingAgentConfig`` carrying model name, batch size,
                max tokens and temperature (sourced from ``config.yaml`` with
                env var overrides).
        """
        self._client = client
        self._config = config

    async def tag(self, chunks: list[dict[str, Any]]) -> list[TaggedChunk]:
        """Tag all chunks. Processes in batches of ``config.batch_size``.

        Args:
            chunks: Output of ``clean_and_chunk()`` -- chunk_index, page,
                is_heading, char_count, text.

        Returns:
            List of ``TaggedChunk`` with relevant, tags, reason added.
        """
        tagged: list[TaggedChunk] = []
        batch_size: int = self._config.batch_size

        for start in range(0, len(chunks), batch_size):
            batch: list[dict[str, Any]] = chunks[start : start + batch_size]
            batch_tagged: list[TaggedChunk] = await self._tag_batch(batch)
            tagged.extend(batch_tagged)
            logger.info("Tagged chunks %d-%d", start, start + len(batch) - 1)

        return tagged

    @agent_retry()
    async def _tag_batch(self, batch: list[dict[str, Any]]) -> list[TaggedChunk]:
        """Send a single batch to the LLM and parse the response.

        Decorated with :func:`agent_retry` so a transient API error or
        malformed-JSON response on a single batch retries that batch only —
        successfully tagged batches are not re-run.

        Args:
            batch: Subset of chunks to tag in one API call.

        Returns:
            List of ``TaggedChunk`` validated from the LLM response.
        """
        response: Message = await self._client.messages.create(
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(batch, ensure_ascii=False, indent=2),
                }
            ],
        )
        raw: str = strip_code_fences(cast(TextBlock, response.content[0]).text)
        try:
            items: list[dict[str, Any]] = json.loads(raw)
        except json.JSONDecodeError:
            # Transient: tenacity will retry. Log raw response for debugging.
            logger.error("Failed to parse tagging response. raw=%.200s", raw)
            raise
        return [TaggedChunk.model_validate(item) for item in items]
