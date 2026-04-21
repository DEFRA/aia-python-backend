from logging import getLogger

import asyncpg
from fastapi import Depends

from app.config import config

logger = getLogger(__name__)

_pool: asyncpg.Pool | None = None

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS document_uploads (
    doc_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_type   TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    status          TEXT NOT NULL,
    uploaded_ts     TIMESTAMPTZ NOT NULL,
    processed_ts    TIMESTAMPTZ,
    result          JSONB
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_filename
    ON document_uploads (user_id, file_name);
"""


async def get_postgres_pool() -> asyncpg.Pool:
    """Return (or create) the module-level asyncpg connection pool."""
    global _pool
    if _pool is None:
        logger.info("Creating PostgreSQL connection pool to %s", config.postgres_uri)
        _pool = await asyncpg.create_pool(config.postgres_uri)
        logger.info("PostgreSQL connection pool created")
    return _pool


async def init_db() -> None:
    """Create tables on startup if they don't already exist."""
    pool = await get_postgres_pool()
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)
    logger.info("PostgreSQL schema initialised")


async def close_postgres_pool() -> None:
    """Gracefully close the connection pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed")


async def get_db_pool(pool: asyncpg.Pool = Depends(get_postgres_pool)) -> asyncpg.Pool:
    """FastAPI Depends-compatible pool dependency."""
    return pool
