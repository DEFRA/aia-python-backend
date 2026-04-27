from pydantic import BaseModel


class UploadResponse(BaseModel):
    documentId: str
    status: str
