from pydantic import BaseModel, model_validator


class DBMLGenerateRequest(BaseModel):
    user_query: str
    model: str
    llm: str
    llm_api_key: str
    base_url: str
    enable_summary: bool = False
    summary: str | None = None
    dbml: str = ""

    # @model_validator(mode="after")
    # def require_summary_when_enabled(self):
    #     if self.enable_summary and not self.summary:
    #         raise ValueError("summary is required when enable_summary is true")
    #     return self


class DBMLGenerateData(BaseModel):
    dbml_query: str
    updated_summary: str | None
    explanation: str
    token_used: int | None


class DBMLGenerateResponse(BaseModel):
    message: str
    status_code: int
    data: DBMLGenerateData
