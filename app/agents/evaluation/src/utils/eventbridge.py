"""EventBridge publisher utility — stage transition choreography.

Provides ``EventBridgePublisher``, the single point through which all
Lambda stage transitions publish events to the ``defra-pipeline`` custom
event bus.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import boto3

from src.config import EventBridgeConfig

logger: logging.Logger = logging.getLogger(__name__)


class EventBridgePublisher:
    """Publishes events to the defra-pipeline EventBridge custom bus.

    Wraps the synchronous ``boto3.client("events").put_events()`` call in
    ``run_in_executor`` to keep it compatible with the async handler pattern
    used across the pipeline.

    No retry logic is implemented here — Lambda + SQS DLQ handles retries.
    """

    def __init__(
        self,
        config: EventBridgeConfig,
        client: Any | None = None,
    ) -> None:
        """Initialise the publisher with an EventBridge config.

        Args:
            config: EventBridge connection settings.  Must be provided
                explicitly — no silent fallback to environment variables.
            client: Pre-configured boto3 ``events`` client.  When ``None``
                (the default), a client is created from *config*.  Pass an
                explicit client in tests to avoid patching boto3 globally.
        """
        self._config: EventBridgeConfig = config
        self._client: Any = (
            client if client is not None else boto3.client("events", region_name=config.region)
        )

    async def publish(self, detail_type: str, detail: dict[str, Any]) -> None:
        """Publish a single event to the defra-pipeline EventBridge bus.

        Builds the standard envelope (``Source``, ``DetailType``, ``Detail``
        as JSON string, ``EventBusName``), calls ``put_events`` via
        ``run_in_executor``, and checks ``FailedEntryCount``.

        Args:
            detail_type: PascalCase event name matching an EventBridge rule
                (e.g. ``"DocumentParsed"``, ``"AllAgentsComplete"``).
            detail: JSON-serialisable dict placed in the event's Detail field.

        Raises:
            RuntimeError: If ``FailedEntryCount`` > 0 in the response.
        """
        entry: dict[str, str] = {
            "Source": self._config.source,
            "DetailType": detail_type,
            "Detail": json.dumps(detail),
            "EventBusName": self._config.bus_name,
        }

        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        response: dict[str, Any] = await loop.run_in_executor(
            None,
            lambda: self._client.put_events(Entries=[entry]),
        )

        if response["FailedEntryCount"] > 0:
            failed: list[dict[str, Any]] = response["Entries"]
            logger.error(
                "EventBridge put_events partial failure: detail_type=%s entries=%s",
                detail_type,
                failed,
            )
            raise RuntimeError(f"EventBridge put_events partial failure: {failed}")

        logger.info(
            "Published event: detail_type=%s bus=%s",
            detail_type,
            self._config.bus_name,
        )
