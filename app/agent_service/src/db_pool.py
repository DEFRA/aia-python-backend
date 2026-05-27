from __future__ import annotations

import asyncpg

from app.agent_service.src.config import DatabaseConfig
from app.agent_service.src.shared.logger import get_logger

logger = get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool(db_config: DatabaseConfig | None = None) -> asyncpg.Pool:
    """Initialize the global connection pool.
    
    Args:
        db_config: DatabaseConfig instance. If None, creates a new one from env.
    
    Returns:
        The initialized asyncpg.Pool.
    """
    global _pool  # noqa: PLW0603
    
    if _pool is not None:
        logger.warning("Connection pool already initialized; returning existing pool")
        return _pool
    
    if db_config is None:
        db_config = DatabaseConfig()
    
    print(f"[agent_service] Database connection string: {db_config.dsn}")
    logger.info("Database connection string: %s", db_config.dsn)
    
    try:
        _pool = await asyncpg.create_pool(
            dsn=db_config.dsn,
            min_size=8,      # Minimum connections to keep open (support 8-9 concurrent agents)
            max_size=30,     # Maximum concurrent connections (headroom for bursts)
            command_timeout=30,  # Timeout per query
            timeout=10,      # Timeout to acquire a connection from the pool
        )
        print("[agent_service] Database connection SUCCESS")
        logger.info(
            "Database connection SUCCESS - pool initialized: min_size=8 max_size=30 dsn=%r",
            db_config.dsn.split("@")[-1] if "@" in db_config.dsn else "***",
        )
    except Exception as e:
        print(f"[agent_service] Database connection FAILED: {e}")
        logger.error("Database connection FAILED: %s", str(e))
        raise
    
    return _pool


async def close_pool() -> None:
    """Close the global connection pool gracefully."""
    global _pool  # noqa: PLW0603
    
    if _pool is None:
        return
    
    await _pool.close()
    _pool = None
    logger.info("Connection pool closed")


def get_pool() -> asyncpg.Pool:
    """Return the initialized global pool. Raises RuntimeError if not initialized."""
    if _pool is None:
        raise RuntimeError(
            "Connection pool not initialized. Call init_pool() on startup."
        )
    return _pool
