import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.config import settings
from src.conversation.api import router as conversation_router
from src.register_application.api import router as application_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(application_router)
app.include_router(conversation_router)


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


@app.get("/health")
def health():
    return {
        "msg": "LLM Gateway is running",
        "status": status.HTTP_200_OK,
        "data": {},
    }
