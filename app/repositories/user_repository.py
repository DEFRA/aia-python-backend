from typing import Optional

import asyncpg

from app.models.user_record import UserRecord
from app.utils.logger import get_logger

logger = get_logger(__name__)

GUEST_USER = UserRecord(
    userId="00000000-0000-0000-0000-000000000001",
    email="guest@aia.local",
    name="Guest User",
)


class UserRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_user_by_id(self, user_id: str) -> Optional[UserRecord]:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT user_id, email, name FROM backend.users WHERE user_id = $1",
                    user_id,
                )
            if row is None:
                return None
            return UserRecord(
                userId=row["user_id"], email=row["email"], name=row["name"]
            )
        except Exception:
            logger.warning(
                "Could not fetch user %s from DB; falling back to guest user", user_id
            )
            return GUEST_USER
