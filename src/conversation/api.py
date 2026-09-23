from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from src.conversation.fallback import NoAvailableLLMError, ProviderRequestError
from src.conversation.schemas import (
    DBMLGenerateData,
    DBMLGenerateRequest,
    DBMLGenerateResponse,
)
from src.conversation.service import generate_dbml, validate_access_token
from src.database import get_db


router = APIRouter(prefix="/api/v1", tags=["DBML"])


@router.post("/generate", response_model=DBMLGenerateResponse)
def generate_dbml_endpoint(
    request: DBMLGenerateRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is required and must be a Bearer token",
        )

    token = authorization.split(" ", 1)[1].strip()
    try:
        validate_access_token(db, token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    try:
        result = generate_dbml(
            db=db,
            user_query=request.user_query,
            enable_summary=request.enable_summary,
            summary=request.summary if request.enable_summary and request.summary else "",
            direct_model=request.model,
            llm=request.llm,
            llm_api_key=request.llm_api_key,
            base_url=request.base_url,
        )

        return DBMLGenerateResponse(
            message="DBML generated successfully",
            status_code=status.HTTP_200_OK,
            data=DBMLGenerateData(**result),
        )

    except NoAvailableLLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ProviderRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
