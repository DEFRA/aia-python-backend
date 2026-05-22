"""Specialist agent registries and structural protocols.

Used by the Agent Service worker to resolve agent class and config by agent type.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.agent_service.src.models.schemas import AgentLLMOutput, QuestionItem
from app.agent_service.src.agents.security_agent import SecurityAgent
from app.agent_service.src.agents.technical_agent import TechnicalAgent
from app.agent_service.src.config import (
    SecurityAgentConfig,
    TechnicalAgentConfig,
)

# ---------------------------------------------------------------------------
# Typed agent / config protocols
# ---------------------------------------------------------------------------


class SpecialistAgentConfig(Protocol):
    """Structural type for any specialist agent's Pydantic config."""

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


# Typed factories for the dispatch registries.
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
