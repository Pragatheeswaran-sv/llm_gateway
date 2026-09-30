from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from time import perf_counter
from uuid import uuid4

from src.conversation.fallback import (
    InvalidDirectAPIKeyError,
    NoAvailableLLMError,
    ProviderRequestError,
)
from src.conversation.schemas import (
    DBMLGenerateData,
    DBMLGenerateRequest,
    DBMLGenerateResponse,
)
from src.conversation.service import (
    _finish_request_log,
    _start_request_log,
    build_user_prompt,
    generate_dbml,
    validate_access_token,
)
from src.database import get_db


router = APIRouter(prefix="/api/v1", tags=["DBML"])


def _redact_secret(value: str, secret: str | None) -> str:
    if secret and len(secret) > 5:
        return value.replace(secret, "[redacted]")
    return value


@router.post("/generate", response_model=DBMLGenerateResponse)
def generate_dbml_endpoint(
    request: DBMLGenerateRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    request_id = uuid4()
    started_at = perf_counter()

    # Determine mode and user-requested values
    direct_values_supplied = any(
        value and value.strip()
        for value in (request.model, request.llm, request.llm_api_key, request.base_url)
    )

    user_prompt = build_user_prompt(
        enable_summary=request.enable_summary,
        summary=request.summary or "",
        user_query=request.user_query,
        dbml=request.dbml,
    )
    request_log = _start_request_log(
        db,
        request_id=request_id,
        user_prompt=user_prompt,
        summary_enabled=request.enable_summary,
        summary=request.summary,
        is_fallback_mode=not direct_values_supplied,
    )
    response_status = status.HTTP_200_OK
    error_message = None
    try:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header is required and must be a Bearer token",
            )

        token = authorization.split(" ", 1)[1].strip()
        validate_access_token(db, token)
        result = generate_dbml(
            db=db,
            dbml=request.dbml,
            user_query=request.user_query,
            enable_summary=request.enable_summary,
            summary=request.summary if request.enable_summary and request.summary else "",
            direct_model=request.model,
            llm=request.llm,
            llm_api_key=request.llm_api_key,
            base_url=request.base_url,
            request_id=request_id,
            request_log=request_log,
        )

        return DBMLGenerateResponse(
            message="DBML generated successfully",
            status_code=status.HTTP_200_OK,
            data=DBMLGenerateData(**result),
        )

    except NoAvailableLLMError as exc:
        response_status = status.HTTP_503_SERVICE_UNAVAILABLE
        error_message = str(exc)
        raise HTTPException(
            status_code=response_status,
            detail=str(exc),
        ) from exc
    except ProviderRequestError as exc:
        response_status = status.HTTP_502_BAD_GATEWAY
        error_message = f"{exc.message} {exc}"
        raise HTTPException(
            status_code=response_status,
            detail=error_message,
        ) from exc
    except InvalidDirectAPIKeyError as exc:
        response_status = status.HTTP_400_BAD_REQUEST
        error_message = str(exc)
        raise HTTPException(status_code=response_status, detail=error_message) from exc
    except ValueError as exc:
        response_status = status.HTTP_401_UNAUTHORIZED if "access token" in str(exc).lower() else status.HTTP_400_BAD_REQUEST
        error_message = _redact_secret(str(exc), request.llm_api_key)
        raise HTTPException(status_code=response_status, detail=error_message) from exc
    except HTTPException as exc:
        response_status = exc.status_code
        error_message = _redact_secret(str(exc.detail), request.llm_api_key)
        raise
    except Exception as exc:
        response_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        error_message = type(exc).__name__
        raise HTTPException(status_code=response_status, detail=str(exc)) from exc
    finally:
        total_duration = round((perf_counter() - started_at) * 1000)
        if response_status >= 400:
            safe_error = _redact_secret(error_message or "", request.llm_api_key) or None
            _finish_request_log(
                db,
                request_log,
                status="request_failed",
                http_status_code=response_status,
                error_message=safe_error,
                duration_ms=total_duration,
            )
        else:
            _finish_request_log(
                db,
                request_log,
                status="success",
                http_status_code=response_status,
                duration_ms=total_duration,
            )

