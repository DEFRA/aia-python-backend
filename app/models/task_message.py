from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class TaskMessage(BaseModel):
    """Message published to the aia-tasks SQS queue consumed by the Agent service."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    task_id: str  # deterministic: "{documentId}_{agentType}"
    document_id: str
    agent_type: str
    template_type: str
    file_content: Optional[str] = None  # null when content exceeds SQS 256 KB limit
    s3_bucket: str
    s3_key: str
