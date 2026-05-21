import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class DocumentSession:
    doc_id: str
    template_type: str
    s3_key: str
    expected_task_ids: set[str]
    collected_results: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completion_event: asyncio.Event = field(default_factory=asyncio.Event)
    replaced: bool = False


class SessionStore:
    """Thread-safe in-memory store tracking per-document agent dispatch state."""

    def __init__(self) -> None:
        self._sessions: dict[str, DocumentSession] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        doc_id: str,
        template_type: str,
        s3_key: str,
        expected_task_ids: set[str],
    ) -> DocumentSession:
        session = DocumentSession(
            doc_id=doc_id,
            template_type=template_type,
            s3_key=s3_key,
            expected_task_ids=expected_task_ids,
        )
        async with self._lock:
            old = self._sessions.get(doc_id)
            if old is not None:
                old.replaced = True
                old.completion_event.set()
            self._sessions[doc_id] = session
        return session

    async def record_result(self, doc_id: str, task_id: str, result: Any) -> bool:
        async with self._lock:
            session = self._sessions.get(doc_id)
            if session is None:
                return False
            if task_id not in session.expected_task_ids:
                return False
            if task_id in session.collected_results:
                return False  # duplicate delivery — already recorded
            session.collected_results[task_id] = result
            all_received = session.expected_task_ids.issubset(
                session.collected_results.keys()
            )
            if all_received:
                session.completion_event.set()
            return all_received

    def get(self, doc_id: str) -> Optional[DocumentSession]:
        return self._sessions.get(doc_id)

    async def remove(self, doc_id: str) -> None:
        async with self._lock:
            self._sessions.pop(doc_id, None)

    @property
    def active_count(self) -> int:
        return len(self._sessions)
