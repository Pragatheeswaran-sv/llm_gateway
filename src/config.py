from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# src/config.py -> project root is one directory above src/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    APP_NAME: str = "LLM Gateway"
    DATABASE_URL: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Keep this stable. Changing it invalidates existing client-secret hashes.
    CLIENT_SECRET_HASH_PEPPER: str

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()  # type: ignore[call-arg]
