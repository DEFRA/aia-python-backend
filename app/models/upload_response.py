from pydantic import BaseModel

class UploadResponse(BaseModel):
    docId: str
    statusCode: int
    errorMessage: str = ""
