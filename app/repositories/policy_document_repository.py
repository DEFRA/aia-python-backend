from typing import Optional

import asyncpg

from app.models.policy_document import PolicyDocumentRecord, PolicyDocumentUpdateRequest


class PolicyDocumentRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def fetch_policy_documents(
        self, page: int = 1, limit: int = 20
    ) -> tuple[list[PolicyDocumentRecord], int]:
        offset = (page - 1) * limit
        async with self.pool.acquire() as conn:
            total_row = await conn.fetchrow(
                "SELECT COUNT(*) AS total FROM data_pipeline.source_policy_docs"
            )
            rows = await conn.fetch(
                """
                SELECT
                    url_id,
                    filename,
                    category,
                    source,
                    url,
                    isactive
                FROM data_pipeline.source_policy_docs
                ORDER BY url_id DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )

        records = [
            PolicyDocumentRecord(
                url_id=row["url_id"],
                filename=row["filename"],
                category=row["category"],
                source=row["source"],
                url=row["url"],
                is_active=row["isactive"],
                updated_at=None,
            )
            for row in rows
        ]
        total = int(total_row["total"]) if total_row else 0
        return records, total

    async def fetch_policy_document_by_url_id(
        self, url_id: int
    ) -> Optional[PolicyDocumentRecord]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    url_id,
                    filename,
                    category,
                    source,
                    url,
                    isactive
                FROM data_pipeline.source_policy_docs
                WHERE url_id = $1
                """,
                url_id,
            )

        if row is None:
            return None

        return PolicyDocumentRecord(
            url_id=row["url_id"],
            filename=row["filename"],
            category=row["category"],
            source=row["source"],
            url=row["url"],
            is_active=row["isactive"],
            updated_at=None,
        )

    async def update_policy_document_by_url_id(
        self, url_id: int, request: PolicyDocumentUpdateRequest
    ) -> Optional[PolicyDocumentRecord]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE data_pipeline.source_policy_docs
                SET
                    filename = $2,
                    category = $3,
                    source = $4,
                    url = $5,
                    isactive = $6
                WHERE url_id = $1
                RETURNING
                    url_id,
                    filename,
                    category,
                    source,
                    url,
                    isactive
                """,
                url_id,
                request.filename,
                request.category,
                request.source,
                request.url,
                request.is_active,
            )

        if row is None:
            return None

        return PolicyDocumentRecord(
            url_id=row["url_id"],
            filename=row["filename"],
            category=row["category"],
            source=row["source"],
            url=row["url"],
            is_active=row["isactive"],
            updated_at=None,
        )
