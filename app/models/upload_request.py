from pydantic import BaseModel

class UploadRequest(BaseModel):
    templateType: str
    fileName: str
