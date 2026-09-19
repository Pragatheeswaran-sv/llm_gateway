from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "LLM Gateway"
    DATABASE_URL: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Keep this stable. Changing it invalidates existing client-secret hashes.
    CLIENT_SECRET_HASH_PEPPER: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# BaseSettings loads required values from .env or the process environment.
settings = Settings()  # type: ignore[call-arg]
