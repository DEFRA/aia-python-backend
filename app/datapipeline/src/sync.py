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


def is_changed(sync_record: dict | None, last_modified: datetime | None) -> bool:
    """Return True if the remote document has changed since the last sync.

    A document is considered changed when:
    - It has never been synced (sync_record is None).
    - The stored last_modified differs from the freshly fetched value.
    - One side has a timestamp and the other does not.
    """
    if sync_record is None:
        return True
    stored: datetime | None = sync_record.get("last_modified")
    if stored is None and last_modified is None:
        return False
    if stored is None or last_modified is None:
        return True
    return stored != last_modified


def upsert_sync_record(
    conn: psycopg2.extensions.connection,
    source_url: str,
    file_name: str,
    last_modified: datetime | None,
    policy_doc_id: str,
) -> None:
    """Insert or update the sync housekeeping record for source_url."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO data_pipeline.policy_document_sync
                (url_hash, source_url, file_name, last_modified, last_synced_at, policy_doc_id)
            VALUES (%s, %s, %s, %s, NOW(), %s)
            ON CONFLICT (url_hash) DO UPDATE
            SET last_modified  = EXCLUDED.last_modified,
                last_synced_at = NOW(),
                policy_doc_id  = EXCLUDED.policy_doc_id
            """,
            (
                url_to_hash(source_url),
                source_url,
                file_name,
                last_modified,
                policy_doc_id,
            ),
        )
    conn.commit()
    logger.debug("Sync record upserted for source_url=%s", source_url)
