from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from src.conversation.schemas import (
    SQLGenerateRequest,
    SQLGenerateResponse,
)
from src.conversation.service import validate_access_token, generate_sql
from src.database import get_db


router = APIRouter(
    prefix="/api/v1/sql",
    tags=["SQL"]
)


@router.post("/generate", response_model=SQLGenerateResponse)
def generate_sql_endpoint(
    request: SQLGenerateRequest,
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
        app = validate_access_token(db, token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    try:
        result = generate_sql(
            db=db,
            app_id=app.id,
            conversation_id=request.conversation_id,
            user_query=request.user_query,
            ai=request.model,
            model=request.llm,
            api_key=request.llm_api_key,
            external_prompt_id=request.external_prompt_id,
        )

        return SQLGenerateResponse(data=result)

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
