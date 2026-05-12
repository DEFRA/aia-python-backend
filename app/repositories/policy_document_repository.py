from typing import Optional

import asyncpg

from app.models.policy_document import (
    PolicyDocumentCreateRequest,
    PolicyDocumentRecord,
    PolicyDocumentUpdateRequest,
)


class PolicyDocumentRepository:
    SOURCE_OPTIONS: tuple[str, ...] = (
        "SharePoint",
        "Confluence",
        "GitHub",
    )

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def delete_policy_document_by_url_id(self, url_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                DELETE FROM data_pipeline.source_policy_docs
                WHERE url_id = $1
                RETURNING url_id
                """,
                url_id,
            )
        return row is not None

    async def create_policy_document(
        self, request: PolicyDocumentCreateRequest
    ) -> PolicyDocumentRecord:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO data_pipeline.source_policy_docs (
                        filename,
                        category,
                        source,
                        url,
                        isactive
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING
                        url_id,
                        filename,
                        category,
                        source,
                        url,
                        isactive,
                        updated_at
                    """,
                    request.filename,
                    request.category,
                    request.source,
                    request.url,
                    request.is_active,
                )
        except asyncpg.UniqueViolationError as exc:
            raise ValueError(
                f"Policy document with URL already exists: {request.url}"
            ) from exc

        return PolicyDocumentRecord(
            url_id=row["url_id"],
            filename=row["filename"],
            category=row["category"],
            source=row["source"],
            url=row["url"],
            is_active=row["isactive"],
            updated_at=row["updated_at"],
        )

    async def category_exists(self, category: str) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1
                FROM data_pipeline.policy_source_categories
                WHERE category = $1 AND isactive = TRUE
                LIMIT 1
                """,
                category,
            )
        return row is not None

    async def fetch_policy_document_options(self) -> tuple[list[str], list[str]]:
        async with self.pool.acquire() as conn:
            category_rows = await conn.fetch(
                """
                SELECT category
                FROM data_pipeline.policy_source_categories
                WHERE isactive = TRUE
                ORDER BY category ASC
                """
            )

        categories = [str(row["category"]) for row in category_rows]
        return list(self.SOURCE_OPTIONS), categories

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
                    isactive,
                    updated_at
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
                updated_at=row["updated_at"],
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
                    isactive,
                    updated_at
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
            updated_at=row["updated_at"],
        )

    async def update_policy_document_by_url_id(
        self, url_id: int, request: PolicyDocumentUpdateRequest
    ) -> Optional[PolicyDocumentRecord]:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE data_pipeline.source_policy_docs
                    SET
                        filename = $2,
                        category = $3,
                        source = $4,
                        url = $5,
                        isactive = $6,
                        updated_at = NOW()
                    WHERE url_id = $1
                    RETURNING
                        url_id,
                        filename,
                        category,
                        source,
                        url,
                        isactive,
                        updated_at
                    """,
                    url_id,
                    request.filename,
                    request.category,
                    request.source,
                    request.url,
                    request.is_active,
                )
        except asyncpg.UniqueViolationError as exc:
            raise ValueError(
                f"Policy document with URL already exists: {request.url}"
            ) from exc

        if row is None:
            return None

        return PolicyDocumentRecord(
            url_id=row["url_id"],
            filename=row["filename"],
            category=row["category"],
            source=row["source"],
            url=row["url"],
            is_active=row["isactive"],
            updated_at=row["updated_at"],
        )
