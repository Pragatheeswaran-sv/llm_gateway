from pydantic import BaseModel, ConfigDict
from typing import Optional

class VisionGenerateRequest(BaseModel):
    file_base64: str
    file_name: str
    model: Optional[str] = None
    llm: Optional[str] = None
    base_url: Optional[str] = None
    llm_api_key: Optional[str] = None

    model_config = ConfigDict(extra="forbid")

class VisionGenerateData(BaseModel):
    dbml_query: str

class VisionGenerateResponse(BaseModel):
    message: str
    status_code: int
    data: VisionGenerateData
