import uuid
from datetime import datetime, timezone


class AppContext:
    """
    Provides system-level context utilities (Timestamps, UUIDs)
    that can be easily mocked in tests to ensure deterministic behavior.
    """

    def generate_uuid(self) -> str:
        return str(uuid.uuid4())

    def get_current_timestamp(self) -> datetime:
        return datetime.now(timezone.utc)
