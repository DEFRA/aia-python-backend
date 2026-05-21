from typing import Optional

import asyncpg
from fastapi import Depends

from ..config.config import config
from .logger import get_logger

logger = get_logger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_postgres_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        logger.info("Creating PostgreSQL connection pool to %s", config.db.uri)
        _pool = await asyncpg.create_pool(config.db.uri)
        logger.info("PostgreSQL connection pool created")
    return _pool


async def init_db() -> None:
    """Database schema initialization is managed via db/init.sql."""
    logger.info("PostgreSQL pool ready")


async def close_postgres_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed")


async def get_db_pool(pool: asyncpg.Pool = Depends(get_postgres_pool)) -> asyncpg.Pool:
    return pool
