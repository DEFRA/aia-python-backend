"""Tests for the specialist agent registries in src.handlers.agent."""

from __future__ import annotations

from src.handlers.agent import (
    AGENT_REGISTRY,
    CONFIG_REGISTRY,
)


def test_agent_registry_contains_only_security_and_technical() -> None:
    """The registries must list exactly the two surviving specialist agents."""
    assert set(AGENT_REGISTRY.keys()) == {"security", "technical"}
    assert set(CONFIG_REGISTRY.keys()) == {"security", "technical"}


def test_agent_module_imports_no_redis_or_eventbridge() -> None:
    """agent.py must not pull in the redis_client or EventBridge publisher."""
    from pathlib import Path

    import src.handlers.agent as agent_module

    source: str = Path(agent_module.__file__).read_text(encoding="utf-8")
    assert "redis_client" not in source
    assert "EventBridgePublisher" not in source
    assert "publish_event" not in source
