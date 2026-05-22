from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class StatusMessage(BaseModel):
    """Message published to the aia-status SQS queue by the Agent service after processing."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    task_id: str = Field(min_length=1, max_length=200)  # "{documentId}_{agentType}"
    document_id: str = Field(min_length=1, max_length=120)
    agent_type: str = Field(min_length=1, max_length=50)
    result: dict[str, Any]
    error: Optional[str] = None
    model_id: Optional[str] = Field(default=None, max_length=200)
    input_tokens: Optional[int] = Field(
        default=None,
        ge=0,
    )  # Aggregated input tokens across all policy docs
    output_tokens: Optional[int] = Field(
        default=None, ge=0
    )  # Aggregated output tokens across all policy docs
