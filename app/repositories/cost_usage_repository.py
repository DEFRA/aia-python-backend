from typing import Any, List

import asyncpg

from app.utils.logger import get_logger

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
                       cu.unit_cost     AS unit_cost
                FROM backend.document_uploads du
                JOIN backend.cost_usage cu
                    ON cu.doc_id = du.doc_id
                WHERE du.user_id = $1
                ORDER BY du.uploaded_ts DESC, du.doc_id, cu.agent_name ASC
                """,
                user_id,
            )

    async def fetch_cost_usage_by_doc(
        self, doc_id: str, user_id: str
    ) -> List[Any]:
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
                       cu.unit_cost     AS unit_cost
                FROM backend.document_uploads du
                JOIN backend.cost_usage cu
                    ON cu.doc_id = du.doc_id
                WHERE du.user_id = $1 AND du.doc_id = $2::uuid
                ORDER BY cu.agent_name ASC
                """,
                user_id,
                doc_id,
            )
