import asyncpg

from ..utils.logger import get_logger

logger = get_logger(__name__)


class CostUsageRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def upsert_cost_usage(
        self,
        doc_id: str,
        agent_name: str,
        input_tokens: int,
        output_tokens: int,
        total_cost_usd: float,
    ) -> None:
        """Insert or update one cost usage row for a doc/agent pair.

        The schema currently has no unique constraint on (doc_id, agent_name),
        so this uses an advisory lock + UPDATE-then-INSERT CTE pattern.
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                WITH lock_row AS (
                    SELECT pg_advisory_xact_lock(hashtext($1 || ':' || $2))
                ),
                updated AS (
                    UPDATE backend.cost_usage
                    SET input_tokens = $3,
                        output_tokens = $4,
                        total_cost_usd = $5
                    WHERE doc_id = $1::uuid
                      AND agent_name = $2
                    RETURNING 1
                )
                INSERT INTO backend.cost_usage (
                    doc_id,
                    agent_name,
                    input_tokens,
                    output_tokens,
                    total_cost_usd
                )
                SELECT $1::uuid, $2, $3, $4, $5
                FROM lock_row
                WHERE NOT EXISTS (SELECT 1 FROM updated)
                """,
                doc_id,
                agent_name,
                input_tokens,
                output_tokens,
                total_cost_usd,
            )
