from pydantic import BaseModel


class SQLGenerateRequest(BaseModel):
    conversation_id: str
    external_prompt_id: str
    user_query: str
    model: str
    llm: str
    llm_api_key: str


class SQLGenerateResponse(BaseModel):
    data: str