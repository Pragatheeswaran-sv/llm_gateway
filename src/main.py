import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.config import settings
from src.database import Base, engine
from src.conversation.api import router as conversation_router
from src.register_application.api import router as application_router
from src.vision_service.api import router as vision_router
from src.export_log_apscheduler import (
    export_logs_router,
    shutdown_scheduler,
    start_scheduler,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        logging.info("Database tables verified/created successfully.")
    except Exception as e:
        logging.error(f"Failed to create database tables: {e}")

    try:
        start_scheduler()
    except Exception as e:
        logging.error(f"Failed to start APScheduler: {e}")

    yield

    try:
        shutdown_scheduler()
    except Exception as e:
        logging.error(f"Failed to shutdown APScheduler: {e}")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(application_router)
app.include_router(conversation_router)
app.include_router(vision_router)
app.include_router(export_logs_router)


def _json_safe(value):
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "message": str(exc.detail),
            "status_code": exc.status_code,
            "error": str(exc.detail),
        },
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "message": "Invalid request payload",
            "status_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
            "error": _json_safe(exc.errors()),
        },
    )


@app.get("/health_check")
@app.get("/health")
def health():
    return {
        "message": "LLM Gateway is running",
        "status_code": status.HTTP_200_OK,
        "data": {},
    }
