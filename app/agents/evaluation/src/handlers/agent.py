"""Specialist agent registries and structural protocols.

Used by the Agent Service worker to resolve agent class and config by agent type.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from src.agents.schemas import AgentLLMOutput, QuestionItem
from src.agents.security_agent import SecurityAgent
from src.agents.technical_agent import TechnicalAgent
from src.config import (
    SecurityAgentConfig,
    TechnicalAgentConfig,
)

# ---------------------------------------------------------------------------
# Typed agent / config protocols
# ---------------------------------------------------------------------------


class SpecialistAgentConfig(Protocol):
    """Structural type for any specialist agent's Pydantic config.

    Every specialist agent config (``SecurityAgentConfig``, ``TechnicalAgentConfig``)
    exposes ``api_key``, ``model``, ``max_tokens`` and ``temperature``; declaring
    them here removes the need for ``Any`` annotations at the dispatch site.
    """

    api_key: str
    model: str
    max_tokens: int
    temperature: float


class SpecialistAgent(Protocol):
    """Structural type for a specialist agent with an async ``assess``."""

    async def assess(
        self,
        document: str,
        questions: list[QuestionItem],
    ) -> AgentLLMOutput: ...


# Typed factories for the dispatch registries. ``Callable[..., T]`` accepts
# any keyword signature (the concrete agents use ``client=`` + ``agent_config=``)
# while preserving a typed return — so ``AGENT_REGISTRY[agent_type](...)`` is
# known to produce a ``SpecialistAgent`` without resorting to ``Any``.
SpecialistAgentFactory = Callable[..., SpecialistAgent]
SpecialistConfigFactory = Callable[..., SpecialistAgentConfig]


# ---------------------------------------------------------------------------
# Agent and config registries
# ---------------------------------------------------------------------------

AGENT_REGISTRY: dict[str, SpecialistAgentFactory] = {
    "security": SecurityAgent,
    "technical": TechnicalAgent,
}

CONFIG_REGISTRY: dict[str, SpecialistConfigFactory] = {
    "security": SecurityAgentConfig,
    "technical": TechnicalAgentConfig,
}
