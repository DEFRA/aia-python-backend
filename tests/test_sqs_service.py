import pytest
from unittest.mock import AsyncMock, patch
from app.models.task_message import TaskMessage
from app.services.sqs_service import SQSService


def _make_task() -> TaskMessage:
    return TaskMessage(
        task_id="doc-1_security",
        document_id="doc-1",
        agent_type="security",
        template_type="SDA",
        file_content="Extracted text",
    )


@pytest.mark.asyncio
async def test_send_task_success():
    sqs_service = SQSService()
    mock_client = AsyncMock()
    mock_client.send_message.return_value = {"MessageId": "msg-123"}

    with patch.object(SQSService, "_get_client") as mock_get_client:
        mock_get_client.return_value.__aenter__.return_value = mock_client

        msg_id = await sqs_service.send_task(_make_task())

        assert msg_id == "msg-123"
        mock_client.send_message.assert_called_once()
        call_kwargs = mock_client.send_message.call_args[1]
        assert "doc-1" in call_kwargs["MessageBody"]


@pytest.mark.asyncio
async def test_send_task_failure():
    sqs_service = SQSService()
    mock_client = AsyncMock()
    mock_client.send_message.side_effect = Exception("SQS Down")

    with patch.object(SQSService, "_get_client") as mock_get_client:
        mock_get_client.return_value.__aenter__.return_value = mock_client

        with pytest.raises(Exception, match="SQS Down"):
            await sqs_service.send_task(_make_task())
