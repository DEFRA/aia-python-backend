from typing import List

import asyncpg

from app.models.cost_usage_record import CostUsageRecord
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CostUsageRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def fetch_cost_usage(self, user_id: str) -> List[CostUsageRecord]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT cu.doc_id           AS "documentId",
                       du.file_name        AS "fileName",
                       cu.agent_name       AS "agentName",
                       cu.input_tokens     AS "inputTokens",
                       cu.output_tokens    AS "outputTokens",
                       cu.unit_cost        AS "unitCost",
                       du.uploaded_ts      AS "uploadedTs",
                       du.processed_ts     AS "processedTs"
                FROM backend.cost_usage cu
                JOIN backend.document_uploads du
                    ON cu.doc_id = du.doc_id::text
                WHERE du.user_id = $1
                ORDER BY du.uploaded_ts DESC
                """,
                user_id,
            )
        return [
            CostUsageRecord(
                documentId=row["documentId"],
                fileName=row["fileName"],
                agentName=row["agentName"],
                inputTokens=row["inputTokens"],
                outputTokens=row["outputTokens"],
                unitCost=row["unitCost"],
                uploadedTs=row["uploadedTs"],
                processedTs=row["processedTs"],
            )
            for row in rows
        ]

    async def fetch_cost_usage_by_doc(
        self, doc_id: str, user_id: str
    ) -> List[CostUsageRecord]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT cu.doc_id           AS "documentId",
                       du.file_name        AS "fileName",
                       cu.agent_name       AS "agentName",
                       cu.input_tokens     AS "inputTokens",
                       cu.output_tokens    AS "outputTokens",
                       cu.unit_cost        AS "unitCost",
                       du.uploaded_ts      AS "uploadedTs",
                       du.processed_ts     AS "processedTs"
                FROM backend.cost_usage cu
                JOIN backend.document_uploads du
                    ON cu.doc_id = du.doc_id::text
                WHERE du.user_id = $1 AND du.doc_id = $2::uuid
                ORDER BY cu.agent_name ASC
                """,
                user_id,
                doc_id,
            )
        return [
            CostUsageRecord(
                documentId=row["documentId"],
                fileName=row["fileName"],
                agentName=row["agentName"],
                inputTokens=row["inputTokens"],
                outputTokens=row["outputTokens"],
                unitCost=row["unitCost"],
                uploadedTs=row["uploadedTs"],
                processedTs=row["processedTs"],
            )
            for row in rows
        ]
