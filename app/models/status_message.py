from typing import Any, Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class StatusMessage(BaseModel):
    """Message published to the aia-status SQS queue by the Agent service after processing."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    task_id: str  # "{documentId}_{agentType}"
    document_id: str
    agent_type: str
    result: dict[str, Any]
    error: Optional[str] = None
    model_id: Optional[str] = None
    input_tokens: Optional[int] = None  # Aggregated input tokens across all policy docs
    output_tokens: Optional[int] = (
        None  # Aggregated output tokens across all policy docs
    )
