"""Tests for src/utils/eventbridge.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.config import EventBridgeConfig
from src.utils.eventbridge import EventBridgePublisher


@pytest.fixture
def config() -> EventBridgeConfig:
    """Return a test EventBridgeConfig."""
    return EventBridgeConfig(
        bus_name="test-bus",
        region="eu-west-2",
    )


def test_publisher_init_creates_boto3_client(config: EventBridgeConfig) -> None:
    """Constructor should store config and create a boto3 events client."""
    with patch("src.utils.eventbridge.boto3") as mock_boto3:
        publisher = EventBridgePublisher(config=config)
        mock_boto3.client.assert_called_once_with("events", region_name="eu-west-2")
        assert publisher._config is config


def test_publisher_init_requires_config() -> None:
    """Constructor should require an explicit EventBridgeConfig argument."""
    with pytest.raises(TypeError):
        EventBridgePublisher()  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_publish_builds_correct_envelope(config: EventBridgeConfig) -> None:
    """publish() should build the standard EventBridge envelope."""
    mock_client = MagicMock()
    mock_client.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

    with patch("src.utils.eventbridge.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        publisher = EventBridgePublisher(config=config)

        await publisher.publish(
            detail_type="DocumentParsed",
            detail={"docId": "doc-123", "chunksCacheKey": "chunks:abc"},
        )

    call_kwargs: dict[str, Any] = mock_client.put_events.call_args[1]
    entries: list[dict[str, Any]] = call_kwargs["Entries"]
    assert len(entries) == 1

    entry: dict[str, Any] = entries[0]
    assert entry["Source"] == "defra.pipeline"
    assert entry["DetailType"] == "DocumentParsed"
    assert entry["EventBusName"] == "test-bus"
    assert '"docId"' in entry["Detail"]


@pytest.mark.asyncio
async def test_publish_raises_on_failed_entry(config: EventBridgeConfig) -> None:
    """publish() should raise RuntimeError when FailedEntryCount > 0."""
    mock_client = MagicMock()
    mock_client.put_events.return_value = {
        "FailedEntryCount": 1,
        "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "boom"}],
    }

    with patch("src.utils.eventbridge.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        publisher = EventBridgePublisher(config=config)

        with pytest.raises(RuntimeError, match="partial failure"):
            await publisher.publish(
                detail_type="DocumentParsed",
                detail={"docId": "doc-123"},
            )


@pytest.mark.asyncio
async def test_publish_calls_put_events_exactly_once(config: EventBridgeConfig) -> None:
    """publish() should call put_events exactly once — no retry logic."""
    mock_client = MagicMock()
    mock_client.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

    publisher = EventBridgePublisher(config=config, client=mock_client)
    await publisher.publish(detail_type="TestEvent", detail={"docId": "doc-1"})

    mock_client.put_events.assert_called_once()
