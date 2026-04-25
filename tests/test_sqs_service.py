import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.sqs_service import SQSService

@pytest.mark.asyncio
async def test_send_task_success():
    # Setup
    sqs_service = SQSService()
    mock_client = AsyncMock()
    mock_client.send_message.return_value = {"MessageId": "msg-123"}
    
    # We patch the _get_client context manager
    with patch.object(SQSService, "_get_client") as mock_get_client:
        mock_get_client.return_value.__aenter__.return_value = mock_client
        
        # Execute
        msg_id = await sqs_service.send_task("doc-1", "Extracted text")
        
        # Verify
        assert msg_id == "msg-123"
        mock_client.send_message.assert_called_once()
        call_args = mock_client.send_message.call_args[1]
        assert "doc-1" in call_args["MessageBody"]
        assert "Extracted text" in call_args["MessageBody"]

@pytest.mark.asyncio
async def test_send_task_failure():
    # Setup
    sqs_service = SQSService()
    mock_client = AsyncMock()
    mock_client.send_message.side_effect = Exception("SQS Down")
    
    with patch.object(SQSService, "_get_client") as mock_get_client:
        mock_get_client.return_value.__aenter__.return_value = mock_client
        
        # Execute & Verify
        with pytest.raises(Exception, match="SQS Down"):
            await sqs_service.send_task("doc-1", "some text")
