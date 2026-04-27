from app.utils.logger import get_logger
from typing import Optional

import asyncpg
from fastapi import Depends

from app.core.config import config

logger = get_logger(__name__)

_pool: Optional[asyncpg.Pool] = None

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id    TEXT        PRIMARY KEY,
    email      TEXT        NOT NULL UNIQUE,
    name       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO users (user_id, email, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'guest@aia.local', 'Guest User')
ON CONFLICT (user_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS document_uploads (
    doc_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    template_type     TEXT        NOT NULL,
    user_id           TEXT        NOT NULL,
    file_name         TEXT        NOT NULL,
    status            TEXT        NOT NULL,
    uploaded_ts       TIMESTAMPTZ NOT NULL,
    processed_ts      TIMESTAMPTZ,
    status_updated_at TIMESTAMPTZ,
    result            JSONB,
    result_md         TEXT,
    error_message     TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_filename
    ON document_uploads (user_id, file_name);
"""

_MIGRATE_SQL_STATEMENTS = [
    "ALTER TABLE document_uploads ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ;",
    "ALTER TABLE document_uploads ADD COLUMN IF NOT EXISTS result_md TEXT;",
    "ALTER TABLE document_uploads ADD COLUMN IF NOT EXISTS error_message TEXT;",
]


async def get_postgres_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        logger.info("Creating PostgreSQL connection pool to %s", config.db.uri)
        _pool = await asyncpg.create_pool(config.db.uri)
        logger.info("PostgreSQL connection pool created")
    return _pool


async def init_db() -> None:
    pool = await get_postgres_pool()
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLES_SQL)
        for statement in _MIGRATE_SQL_STATEMENTS:
            try:
                await conn.execute(statement)
            except asyncpg.exceptions.DuplicateColumnError:
                pass
    logger.info("PostgreSQL schema initialised")


async def close_postgres_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed")


async def get_db_pool(pool: asyncpg.Pool = Depends(get_postgres_pool)) -> asyncpg.Pool:
    return pool
