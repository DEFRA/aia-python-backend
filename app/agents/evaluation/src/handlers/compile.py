"""Stage 7 — Compile Lambda handler.

Triggered by EventBridge (AgentCompleted). Waits for all agents, compiles results,
and publishes ResultsCompiled.
Full implementation: plans/07-compile-lambda.md
"""
from __future__ import annotations

import asyncio
from typing import Any


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    return asyncio.run(_handler(event, context))


async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    raise NotImplementedError("See plans/07-compile-lambda.md")
