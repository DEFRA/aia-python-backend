from pydantic import BaseModel


class UserRecord(BaseModel):
    userId: str
    email: str
    name: str
