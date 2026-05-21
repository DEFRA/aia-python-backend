"""Tagging agent -- applies security/governance tags to document chunks."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import anthropic
from anthropic.types import TextBlock

from app.agent_service.src.agents.prompts.tagging import SYSTEM_PROMPT
from app.agent_service.src.models.schemas import TaggedChunk
from app.agent_service.src.config import TaggingAgentConfig
from app.agent_service.src.utils.helpers import strip_code_fences

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
        self._client = client
        self._config = config

    async def tag(self, chunks: list[dict[str, Any]]) -> list[TaggedChunk]:
        """Tag all chunks. Processes in batches of ``config.batch_size``."""
        tagged: list[TaggedChunk] = []
        batch_size: int = self._config.batch_size

        for start in range(0, len(chunks), batch_size):
            batch: list[dict[str, Any]] = chunks[start : start + batch_size]
            batch_tagged: list[TaggedChunk] = await self._tag_batch(batch)
            tagged.extend(batch_tagged)
            logger.info("Tagged chunks %d-%d", start, start + len(batch) - 1)

        return tagged

    async def _tag_batch(self, batch: list[dict[str, Any]]) -> list[TaggedChunk]:
        """Send a single batch to the LLM and parse the response."""
        response = await self._client.messages.create(
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
        items: list[dict[str, Any]] = json.loads(raw)
        return [TaggedChunk.model_validate(item) for item in items]
