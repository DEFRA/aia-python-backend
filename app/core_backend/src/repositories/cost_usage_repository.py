from typing import Any, List

import asyncpg

from utils.logger import get_logger

logger = get_logger(__name__)


class CostUsageRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def fetch_all_cost_usage(self, user_id: str) -> List[Any]:
        """Return every cost-usage row for the user, joined to its document.

        Aggregation, grouping and pagination are handled by the service layer
        so the database only has to do a single indexed scan + join.
        """
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT du.doc_id::text  AS doc_id,
                       du.file_name     AS file_name,
                       du.uploaded_ts   AS uploaded_ts,
                       cu.agent_name    AS agent_name,
                       cu.input_tokens  AS input_tokens,
                       cu.output_tokens AS output_tokens,
                      cu.total_cost_usd AS total_cost_usd
                FROM backend.document_uploads du
                JOIN backend.cost_usage cu
                    ON cu.doc_id = du.doc_id
                WHERE du.user_id = $1
                ORDER BY du.uploaded_ts DESC, du.doc_id, cu.agent_name ASC
                """,
                user_id,
            )

    async def fetch_cost_usage_by_doc(self, doc_id: str, user_id: str) -> List[Any]:
        """Return every cost-usage row for a single document owned by the user.

        Returns an empty list if the document does not belong to the user or
        has no cost rows.
        """
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT du.doc_id::text  AS doc_id,
                       du.file_name     AS file_name,
                       du.uploaded_ts   AS uploaded_ts,
                       cu.agent_name    AS agent_name,
                       cu.input_tokens  AS input_tokens,
                       cu.output_tokens AS output_tokens,
                      cu.total_cost_usd AS total_cost_usd
                FROM backend.document_uploads du
                JOIN backend.cost_usage cu
                    ON cu.doc_id = du.doc_id
                WHERE du.user_id = $1 AND du.doc_id = $2::uuid
                ORDER BY cu.agent_name ASC
                """,
                user_id,
                doc_id,
            )

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
