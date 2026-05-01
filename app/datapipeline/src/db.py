from __future__ import annotations

import json
import logging
from pathlib import Path

import psycopg2
import psycopg2.extensions
from psycopg2.extras import RealDictCursor

from app.datapipeline.src.schemas import ExtractedQuestion, PolicySource
from app.datapipeline.src.utils import new_uuid

logger = logging.getLogger(__name__)


def load_local_policy_sources(path: str | Path) -> list[PolicySource]:
    """Load policy sources from a local JSON file (feature-flag mode).

    Mirrors fetch_policy_sources() — only entries with isactive=true are returned.
    The file must be a JSON array whose items match the PolicySource schema.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    sources = [PolicySource.model_validate(item) for item in data]
    active = [s for s in sources if s.isactive]
    logger.info("Loaded %d active policy source(s) from %s", len(active), path)
    return active


def fetch_policy_sources(conn: psycopg2.extensions.connection) -> list[PolicySource]:
    """Fetch all active policy source URLs from data_pipeline.source_policy_docs."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT url_id, url, filename, category, type, isactive
            FROM data_pipeline.source_policy_docs
            WHERE isactive = TRUE
            ORDER BY url_id
            """
        )
        rows = cur.fetchall()
    logger.info("Fetched %d active policy source(s)", len(rows))
    return [PolicySource(**dict(row)) for row in rows]


def fetch_all_policy_sources(conn: psycopg2.extensions.connection) -> list[PolicySource]:
    """Fetch ALL policy sources (active and inactive) from data_pipeline.source_policy_docs."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT url_id, url, filename, category, type, isactive
            FROM data_pipeline.source_policy_docs
            ORDER BY url_id
            """
        )
        rows = cur.fetchall()
    active = sum(1 for r in rows if r["isactive"])
    logger.info("Fetched %d policy source(s) (%d active, %d inactive)", len(rows), active, len(rows) - active)
    return [PolicySource(**dict(row)) for row in rows]


def delete_policy_document_by_url(
    conn: psycopg2.extensions.connection,
    url: str,
) -> int:
    """Delete the policy_documents row for url and return the number of rows deleted (0 or 1).

    Cascade behaviour (defined in init.sql):
      - questions              ON DELETE CASCADE  → deleted automatically
      - question_categories    ON DELETE CASCADE  → deleted automatically
      - policy_document_sync   ON DELETE SET NULL → policy_doc_id nulled, sync record kept
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM data_pipeline.policy_documents WHERE source_url = %s",
            (url,),
        )
        count: int = cur.rowcount
    conn.commit()
    if count:
        logger.info("Deleted policy_document and cascaded questions/categories for url=%s", url)
    return count


def insert_policy_document(
    conn: psycopg2.extensions.connection,
    source_url: str,
    file_name: str,
    category: str,
) -> str:
    """Upsert a policy document record and return its policy_doc_id.

    Uses ON CONFLICT to handle re-runs — if the URL already exists the
    file_name and category are updated and the existing policy_doc_id is returned.
    """
    policy_doc_id = new_uuid()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO data_pipeline.policy_documents (policy_doc_id, source_url, file_name, category)
            VALUES (%s::uuid, %s, %s, %s)
            ON CONFLICT (source_url) DO UPDATE
                SET file_name = EXCLUDED.file_name,
                    category  = EXCLUDED.category
            RETURNING policy_doc_id::text
            """,
            (policy_doc_id, source_url, file_name, category),
        )
        result = cur.fetchone()
        returned_id: str = result[0]
    conn.commit()
    logger.info("Upserted policy_document policy_doc_id=%s url=%s category=%s", returned_id, source_url, category)
    return returned_id


def delete_questions_for_doc(
    conn: psycopg2.extensions.connection,
    policy_doc_id: str,
) -> int:
    """Delete all questions for a policy document (cascade removes categories).

    Returns the number of rows deleted.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM data_pipeline.questions WHERE policy_doc_id = %s::uuid",
            (policy_doc_id,),
        )
        count: int = cur.rowcount
    conn.commit()
    logger.info("Deleted %d stale question(s) for policy_doc_id=%s", count, policy_doc_id)
    return count


def insert_questions(
    conn: psycopg2.extensions.connection,
    policy_doc_id: str,
    questions: list[ExtractedQuestion],
) -> int:
    """Batch-insert questions and their category mappings.

    Each question gets a new UUID. Categories are inserted into
    question_categories (junction table) with ON CONFLICT DO NOTHING
    so partial re-runs are safe.

    Returns:
        Number of questions inserted.
    """
    count = 0
    with conn.cursor() as cur:
        for q in questions:
            question_id = new_uuid()
            cur.execute(
                """
                INSERT INTO data_pipeline.questions
                    (question_id, question_text, reference, source_excerpt, policy_doc_id)
                VALUES (%s::uuid, %s, %s, %s, %s::uuid)
                """,
                (question_id, q.question_text, q.reference, q.source_excerpt, policy_doc_id),
            )
            count += 1
    conn.commit()
    logger.info("Inserted %d question(s) for policy_doc_id=%s", count, policy_doc_id)
    return count
