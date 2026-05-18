import json

import pytest


def _valid_status_payload() -> dict[str, object]:
    return {
        "taskId": "doc1_security",
        "documentId": "doc1",
        "agentType": "security",
        "result": {"agent_type": "security", "docs": []},
        "modelId": "claude-3-5-haiku-20241022",
        "inputTokens": 12,
        "outputTokens": 7,
    }


def test_parse_status_message_accepts_valid_payload() -> None:
    from app.orchestrator.main import _parse_status_message

    payload = _valid_status_payload()
    status_msg = _parse_status_message(json.dumps(payload))

    assert status_msg.task_id == "doc1_security"
    assert status_msg.agent_type == "security"
    assert status_msg.input_tokens == 12
    assert status_msg.output_tokens == 7


def test_parse_status_message_rejects_mismatched_task_id() -> None:
    from app.orchestrator.main import (
        NonRetriableStatusMessageError,
        _parse_status_message,
    )

    payload = _valid_status_payload()
    payload["taskId"] = "doc1_technical"

    with pytest.raises(NonRetriableStatusMessageError, match="task_id"):
        _parse_status_message(json.dumps(payload))


def test_parse_status_message_rejects_negative_tokens() -> None:
    from app.orchestrator.main import (
        NonRetriableStatusMessageError,
        _parse_status_message,
    )

    payload = _valid_status_payload()
    payload["inputTokens"] = -1

    with pytest.raises(NonRetriableStatusMessageError, match="validation failed"):
        _parse_status_message(json.dumps(payload))


def test_parse_status_message_rejects_unknown_agent_type() -> None:
    from app.orchestrator.main import (
        NonRetriableStatusMessageError,
        _parse_status_message,
    )

    payload = _valid_status_payload()
    payload["agentType"] = "totally_unknown"
    payload["taskId"] = "doc1_totally_unknown"

    with pytest.raises(NonRetriableStatusMessageError, match="Unknown agent_type"):
        _parse_status_message(json.dumps(payload))


def test_parse_status_message_rejects_extra_fields() -> None:
    from app.orchestrator.main import (
        NonRetriableStatusMessageError,
        _parse_status_message,
    )

    payload = _valid_status_payload()
    payload["poisonField"] = "inject"

    with pytest.raises(NonRetriableStatusMessageError, match="validation failed"):
        _parse_status_message(json.dumps(payload))
