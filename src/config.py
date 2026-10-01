import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)


class Settings:
    APP_NAME = os.getenv("APP_NAME", "LLM Gateway")
    DATABASE_URL = os.getenv("DATABASE_URL")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    CLIENT_SECRET_HASH_PEPPER = os.getenv("CLIENT_SECRET_HASH_PEPPER")
    API_KEY_ENCRYPTION_KEY = os.getenv("API_KEY_ENCRYPTION_KEY")

    # Export & Email Scheduler Settings
    ALERT_EMAIL_RECIPIENT = os.getenv("ALERT_EMAIL_RECIPIENT", "kesavan.t@mitrahsoft.in")
    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    DB_CLEANUP_THRESHOLD_MB = float(os.getenv("DB_CLEANUP_THRESHOLD_MB", "400.0"))
    LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "14"))


settings = Settings()