from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class PolicyDocumentRecord(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    url_id: int
    filename: str
    category: str
    source: str
    url: str
    is_active: bool
    updated_at: Optional[str] = None


class PolicyDocumentListResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    documents: list[PolicyDocumentRecord]
    total: int
    page: int
    limit: int


class PolicyDocumentUpdateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    filename: str = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=4000)
    is_active: bool
