import uuid
from datetime import datetime, timezone


class AppContext:
    """Provides system context utilities that are easy to mock in tests."""

    def generate_uuid(self) -> str:
        return str(uuid.uuid4())

    def get_current_timestamp(self) -> datetime:
        return datetime.now(timezone.utc)
