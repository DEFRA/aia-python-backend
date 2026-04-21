"""Stage 8a — Persist Lambda handler.

Triggered by EventBridge (ResultsCompiled). Persists assessment results to PostgreSQL
and publishes ResultsPersisted.
Full implementation: plans/08-persist-and-move-lambdas.md
"""
from __future__ import annotations

import asyncio
from typing import Any


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    return asyncio.run(_handler(event, context))


async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    raise NotImplementedError("See plans/08-persist-and-move-lambdas.md")
