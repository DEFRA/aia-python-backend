"""Stage 8b — S3 Move Lambda handler.

Triggered by EventBridge (ResultsCompiled). Moves the processed S3 object to the
output prefix and publishes S3ObjectMoved.
Full implementation: plans/08-persist-and-move-lambdas.md
"""

from __future__ import annotations

import asyncio
from typing import Any


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    return asyncio.run(_handler(event, context))


async def _handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    raise NotImplementedError("See plans/08-persist-and-move-lambdas.md")
