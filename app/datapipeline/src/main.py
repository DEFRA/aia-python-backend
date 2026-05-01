"""Data Pipeline — main orchestrator.

Reads active policy source URLs from data_pipeline.source_path_policydoc,
fetches each SharePoint page, extracts evaluation questions via LLM,
and writes the results to the Phase 1 normalised tables:
  data_pipeline.policy_documents
  data_pipeline.questions
  data_pipeline.question_categories
  data_pipeline.policy_document_sync  (housekeeping / change-detection)

Feature flag — local source list:
  Set USE_LOCAL_POLICY_SOURCES=true to read policy URLs from a bundled JSON
  file instead of the database.  Useful for development and testing without a
  live data_pipeline.source_path_policydoc table.  The default file path is
  app/datapipeline/data/policy_sources.json; override with
  LOCAL_POLICY_SOURCES_PATH=<absolute path>.

Run locally:
    python -m app.datapipeline.src.main

Deployed as an AWS Lambda (see lambda_function.py for the handler wrapper).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

from app.datapipeline.src.db import (
    delete_questions_for_doc,
    fetch_policy_sources,
    insert_policy_document,
    insert_questions,
    load_local_policy_sources,
)
from app.datapipeline.src.evaluator import QuestionExtractor
from app.datapipeline.src.sharepoint import SharePointClient
from app.datapipeline.src.sync import get_sync_record, is_changed, upsert_sync_record
from app.datapipeline.src.utils import page_name_from_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

_REQUIRED_ENV = [
    "DB_HOST",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "SHAREPOINT_TENANT_ID",
    "SHAREPOINT_CLIENT_ID",
    "SHAREPOINT_CLIENT_SECRET",
    "AWS_DEFAULT_REGION",
    "MODEL_ID",
]

_DEFAULT_LOCAL_SOURCES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "policy_sources.json"
)


def _load_sources(conn: psycopg2.extensions.connection) -> list:
    """Return policy sources from the DB or from the local JSON file.

    Controlled by USE_LOCAL_POLICY_SOURCES env var (default: false).
    When true, LOCAL_POLICY_SOURCES_PATH overrides the bundled file location.
    """
    if os.environ.get("USE_LOCAL_POLICY_SOURCES", "false").lower() == "true":
        path = os.environ.get(
            "LOCAL_POLICY_SOURCES_PATH", str(_DEFAULT_LOCAL_SOURCES_PATH)
        )
        logger.info(
            "Feature flag USE_LOCAL_POLICY_SOURCES=true — loading from %s", path
        )
        return load_local_policy_sources(path)
    return fetch_policy_sources(conn)


def _get_db_connection() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def _build_sharepoint_client() -> SharePointClient:
    return SharePointClient(
        tenant_id=os.environ["SHAREPOINT_TENANT_ID"],
        client_id=os.environ["SHAREPOINT_CLIENT_ID"],
        client_secret=os.environ["SHAREPOINT_CLIENT_SECRET"],
    )


def _build_extractor() -> QuestionExtractor:
    return QuestionExtractor(
        aws_access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
        aws_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
        aws_region=os.environ["AWS_DEFAULT_REGION"],
        model_id=os.environ["MODEL_ID"],
    )


def run() -> dict[str, int]:
    """Execute the full data pipeline and return a summary dict.

    Returns:
        {"processed": N, "skipped": N, "failed": N}
    """
    for var in _REQUIRED_ENV:
        if not os.environ.get(var):
            raise RuntimeError(f"Missing required environment variable: {var}")

    conn = _get_db_connection()
    sp = _build_sharepoint_client()
    extractor = _build_extractor()

    try:
        sources = _load_sources(conn)
    except Exception as exc:
        conn.close()
        raise RuntimeError(f"Failed to fetch policy sources: {exc}") from exc

    if not sources:
        logger.warning("No active policy sources found — nothing to process.")
        conn.close()
        return {"processed": 0, "skipped": 0, "failed": 0}

    processed = skipped = failed = 0

    for source in sources:
        url = source.url
        logger.info("=== Processing policy source url=%s ===", url)

        # 1. Fetch SharePoint page content and last_modified timestamp
        try:
            content, last_modified = sp.read_page_content(url)
        except Exception as exc:
            logger.error("SharePoint fetch failed url=%s: %s", url, exc)
            failed += 1
            continue

        # 2. Sync check — skip if content has not changed
        sync = get_sync_record(conn, url)
        if not is_changed(sync, last_modified):
            logger.info("No change detected, skipping url=%s", url)
            skipped += 1
            continue

        file_name = page_name_from_url(url)

        # 3. Extract questions via LLM
        try:
            questions = extractor.extract(url, content, source.category)
        except Exception as exc:
            logger.error("Question extraction failed url=%s: %s", url, exc)
            failed += 1
            continue

        if not questions:
            logger.warning(
                "LLM returned 0 questions for url=%s — skipping DB write", url
            )
            failed += 1
            continue

        # 4. Persist to Phase 1 tables (replace, not accumulate)
        try:
            policy_doc_id = insert_policy_document(conn, url, file_name)
            delete_questions_for_doc(conn, policy_doc_id)
            insert_questions(conn, policy_doc_id, questions)
            upsert_sync_record(conn, url, file_name, last_modified, policy_doc_id)
        except Exception as exc:
            logger.error("DB write failed url=%s: %s", url, exc)
            conn.rollback()
            failed += 1
            continue

        logger.info(
            "Done url=%s questions=%d policy_doc_id=%s",
            url,
            len(questions),
            policy_doc_id,
        )
        processed += 1

    conn.close()
    summary = {"processed": processed, "skipped": skipped, "failed": failed}
    logger.info("Pipeline complete: %s", summary)
    return summary


def main() -> None:
    try:
        run()
    except Exception as exc:
        logger.critical("Pipeline failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
