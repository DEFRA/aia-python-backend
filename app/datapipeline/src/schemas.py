from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PolicySource(BaseModel):
    url_id: int
    url: str
    filename: str
    category: str
    type: str
    isactive: bool


class ExtractedQuestion(BaseModel):
    question_text: str
    reference: str  # e.g. "Section 3.2", "C1.a"
    source_excerpt: str  # verbatim passage from the policy document


class SyncRecord(BaseModel):
    url_hash: str
    source_url: str
    last_modified: datetime | None
    content_size: int | None
    last_synced_at: datetime
    policy_doc_id: str | None
