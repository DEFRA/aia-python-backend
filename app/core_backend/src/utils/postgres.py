from utils.logger import get_logger
from typing import Optional

import asyncpg
from fastapi import Depends

from config import config

logger = get_logger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_postgres_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        logger.info("Creating PostgreSQL connection pool to %s", config.db.uri)
        _pool = await asyncpg.create_pool(config.db.uri)
        logger.info("PostgreSQL connection pool created")
    return _pool


# Database schema is now deployed externally via pipeline.
# init_db is no longer responsible for running schema SQL scripts.
async def init_db() -> None:
    logger.info(
        "init_db() called, but schema is now managed by deployment pipeline. No action taken."
    )


async def close_postgres_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed")


async def get_db_pool(pool: asyncpg.Pool = Depends(get_postgres_pool)) -> asyncpg.Pool:
    return pool
