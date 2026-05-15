from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse


def url_to_hash(url: str) -> str:
    """SHA-256 hex digest of the URL — used as policy_document_sync PK."""
    return hashlib.sha256(url.encode()).hexdigest()


def new_uuid() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def page_name_from_url(url: str) -> str:
    """Derive a short display name from a SharePoint URL.

    Uses the last non-empty path segment so the stored file_name is
    human-readable (e.g. 'DataProtectionPolicy' instead of a raw URL).
    Falls back to 'page' if the path has no meaningful segment.
    """
    path = urlparse(url).path.rstrip("/")
    segment = path.split("/")[-1] if "/" in path else path
    return segment or "page"
