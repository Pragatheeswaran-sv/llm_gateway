from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPORTED_PROVIDERS = {"groq", "openai", "gemini", "claude", "kimi", "freellmapi"}


class DBMLGenerateRequest(BaseModel):
    """Generate DBML using either direct credentials or database fallback."""

    model_config = ConfigDict(extra="forbid")

    user_query: str = Field(min_length=1)
    dbml: str = ""
    enable_summary: bool = False
    summary: str | None = None
    model: str | None = None
    llm: str | None = None
    llm_api_key: str | None = None
    base_url: str | None = None

    @model_validator(mode="after")
    def validate_direct_provider_fields(self):
        fields = ("model", "llm", "llm_api_key", "base_url")
        for name in fields:
            value = getattr(self, name)
            if value is not None:
                stripped = value.strip()
                setattr(self, name, stripped or None)

        if self.model and self.model.lower() not in SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise ValueError(
                f"Unsupported provider '{self.model}'. Supported providers: {supported}."
            )

        if self.base_url:
            try:
                url = urlsplit(self.base_url)
                valid = (
                    url.scheme in {"http", "https"}
                    and bool(url.hostname)
                    and url.username is None
                    and url.password is None
                    and not url.query
                    and not url.fragment
                    and not any(char.isspace() for char in self.base_url)
                )
                url.port  # Validate the port, if supplied.
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(
                    "base_url must be a plain HTTP or HTTPS URL without credentials, "
                    "query parameters, or fragments; do not use Markdown link formatting"
                )

        return self


class DBMLGenerateData(BaseModel):
    dbml_query: str
    updated_summary: str | None
    explanation: str
    token_used: int | None


class DBMLGenerateResponse(BaseModel):
    message: str
    status_code: int
    data: DBMLGenerateData
