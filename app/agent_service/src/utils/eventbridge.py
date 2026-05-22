"""EventBridge publisher utility — stage transition choreography."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import boto3

from app.agent_service.src.config import EventBridgeConfig

logger: logging.Logger = logging.getLogger(__name__)


class EventBridgePublisher:
    """Publishes events to the defra-pipeline EventBridge custom bus."""

    def __init__(
        self,
        config: EventBridgeConfig,
        client: Any | None = None,
    ) -> None:
        self._config: EventBridgeConfig = config
        self._client: Any = (
            client if client is not None else boto3.client("events", region_name=config.region)
        )

    async def publish(self, detail_type: str, detail: dict[str, Any]) -> None:
        """Publish a single event to the defra-pipeline EventBridge bus."""
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
