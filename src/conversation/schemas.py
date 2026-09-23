from pydantic import BaseModel, ConfigDict, Field, model_validator


class DBMLGenerateRequest(BaseModel):
    """Generate DBML using either direct credentials or database fallback."""

    model_config = ConfigDict(extra="forbid")

    user_query: str = Field(min_length=1)
    enable_summary: bool = False
    summary: str | None = None
    model: str | None = None
    llm: str | None = None
    llm_api_key: str | None = None
    base_url: str | None = None

    @model_validator(mode="after")
    def validate_direct_provider_fields(self):
        fields = {
            "model": self.model,
            "llm": self.llm,
            "llm_api_key": self.llm_api_key,
            "base_url": self.base_url,
        }
        supplied = {name: value for name, value in fields.items() if value and value.strip()}
        if supplied and len(supplied) != len(fields):
            missing = ", ".join(name for name, value in fields.items() if not value or not value.strip())
            raise ValueError(
                "model, llm, llm_api_key, and base_url must all be provided "
                f"when using direct provider mode; missing: {missing}"
            )

        for name, value in fields.items():
            if value is not None:
                stripped = value.strip()
                setattr(self, name, stripped or None)
        return self


class DBMLGenerateData(BaseModel):
    dbml_query: str
    updated_summary: str | None
    explanation: str
    used_tokens: int = 0


class DBMLGenerateResponse(BaseModel):
    message: str
    status_code: int
    data: DBMLGenerateData
