from typing import Any, Dict, Optional
from pydantic import BaseModel

class ResultRecord(BaseModel):
    file_name: str
    result: Optional[Dict[str, Any]] = None
