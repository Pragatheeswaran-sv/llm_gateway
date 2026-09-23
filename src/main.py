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
    detail = str(exc.detail)
    message = detail
    if isinstance(exc.detail, dict) and {"message", "error"} <= exc.detail.keys():
        message = str(exc.detail["message"])
        detail = str(exc.detail["error"])
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "message": message,
            "status_code": exc.status_code,
            "data": {"error": detail},
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = _json_safe(exc.errors())
    message = "Invalid request payload"

    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first_error = errors[0]
        context = first_error.get("ctx")
        context_error = context.get("error") if isinstance(context, dict) else None
        message = str(context_error or first_error.get("msg") or message)
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        location = first_error.get("loc", ())
        field = ".".join(str(part) for part in location if part != "body")
        if field:
            message = f"{field}: {message}"

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "message": message,
            "status_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
            "data": {"error": message},
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "message": "Internal server error",
            "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "data": {"error": str(exc)},
        },
    )

@app.get("/health")
def health():
    return {
        "message": "LLM Gateway is running",
        "status_code": status.HTTP_200_OK,
        "data": {},
    }
