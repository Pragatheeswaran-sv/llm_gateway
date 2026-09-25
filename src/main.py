from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.config import get_logger
from src.database import Base, engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LLM gateway starting up")
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables verified/created successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database tables: {e}")
    yield
    logger.info("LLM gateway shutting down")


app = FastAPI(
    title="LLM GATEWAY",
    version="0.1.0",
    lifespan=lifespan,
)

@app.get("/health_check")
def health_check():
    return {"message": "LLM gateway is running"}
