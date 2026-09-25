from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.database import get_db
from src.register_application.schemas import (
    ApiResponse,
    RegisterData,
    RegisterRequest,
    TokenData,
    TokenRequest,
)
from src.register_application.service import (
    InvalidClientCredentialsError,
    RegistrationConflictError,
    issue_access_token,
    register_application,
)


router = APIRouter(prefix="/api/v1", tags=["Application"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[RegisterData],
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        application, raw_secret = register_application(db, payload)
    except RegistrationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return {
        "message": "application registered successfully",
        "status_code": status.HTTP_201_CREATED,
        "data": {
            "client_id": application.client_id,
            "client_secret_key": raw_secret,
            "app_name": application.app_name,
        },
    }


@router.post(
    "/token",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[TokenData],
)
def token(payload: TokenRequest, db: Session = Depends(get_db)):
    try:
        access_token, expires_at = issue_access_token(db, payload)
    except InvalidClientCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return {
        "message": "Token issued successfully",
        "status_code": status.HTTP_200_OK,
        "data": {
            "access_token": access_token,
            "time_expires": expires_at,
        },
    }
