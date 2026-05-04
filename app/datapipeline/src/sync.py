from __future__ import annotations

import logging
import psycopg2
import psycopg2.extensions
from datetime import datetime
from psycopg2.extras import RealDictCursor
from app.datapipeline.src.utils import url_to_hash

logger = logging.getLogger(__name__)


def get_sync_record(
    conn: psycopg2.extensions.connection,
    source_url: str,
) -> dict | None:
    """Return the sync record for source_url, or None if not yet synced."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM data_pipeline.policy_document_sync WHERE url_hash = %s",
            (url_to_hash(source_url),),
        )
        return cur.fetchone()


def is_changed(
    sync_record: dict | None,
    last_modified: datetime | None,
    content_size: int,
) -> bool:
    """Return True if the remote document has changed since the last sync.

    Uses two independent signals — last_modified timestamp and content byte
    count — so that a change is detected even when one signal is unreliable
    (e.g. SharePoint pages that omit or stale the last_modified header).

    Rules:
    - Never synced (sync_record is None) → changed.
    - Both timestamps absent → compare content_size only.
    - One timestamp absent, the other present → changed.
    - Timestamps differ → changed.
    - Timestamps match but content_size differs (or stored size unknown) → changed.
    - Both timestamps and content_size match → not changed.
    """
    if sync_record is None:
        return True
    stored_ts: datetime | None = sync_record.get("last_modified")
    stored_size: int | None = sync_record.get("content_size")
    if stored_ts is None and last_modified is None:
        # No timestamp signal — rely on content_size alone
        return stored_size != content_size
    if stored_ts is None or last_modified is None:
        return True
    if stored_ts != last_modified:
        return True
    # Timestamps match — use content_size as tiebreaker
    return stored_size != content_size


def upsert_sync_record(
    conn: psycopg2.extensions.connection,
    source_url: str,
    last_modified: datetime | None,
    content_size: int,
    policy_doc_id: str,
) -> None:
    """Insert or update the sync housekeeping record for source_url."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO data_pipeline.policy_document_sync
                (url_hash, source_url, last_modified, content_size, last_synced_at, policy_doc_id)
            VALUES (%s, %s, %s, %s, NOW(), %s)
            ON CONFLICT (url_hash) DO UPDATE
            SET last_modified  = EXCLUDED.last_modified,
                content_size   = EXCLUDED.content_size,
                last_synced_at = NOW(),
                policy_doc_id  = EXCLUDED.policy_doc_id
            """,
            (
                url_to_hash(source_url),
                source_url,
                last_modified,
                content_size,
                policy_doc_id,
            ),
        )
    conn.commit()
    logger.debug("Sync record upserted for source_url=%s", source_url)
