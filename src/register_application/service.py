from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.register_application.models import RegisterApplication
from src.register_application.schemas import RegisterRequest, TokenRequest
from src.utils.helper import (
    create_access_token,
    generate_client_id,
    generate_client_secret_key,
    hash_client_secret_key,
    verify_client_secret_key,
)


class RegistrationConflictError(ValueError):
    pass


class InvalidClientCredentialsError(ValueError):
    pass


def register_application(db: Session, payload: RegisterRequest) -> tuple[RegisterApplication, str]:
    existing = db.scalar(
        select(RegisterApplication).where(RegisterApplication.app_name == payload.name)
    )
    if existing is not None:
        raise RegistrationConflictError("Application name already exists")

    raw_secret = generate_client_secret_key()

    application = RegisterApplication(
        app_name=payload.name,
        description=payload.description,
        client_id=generate_client_id(),
        client_secret_key_hash=hash_client_secret_key(raw_secret),
        is_active=True,
    )

    db.add(application)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RegistrationConflictError("Application registration already exists") from exc

    db.refresh(application)
    return application, raw_secret


def issue_access_token(db: Session, payload: TokenRequest) -> tuple[str, object]:
    application = db.scalar(
        select(RegisterApplication).where(RegisterApplication.client_id == payload.client_id)
    )

    if application is None:
        raise InvalidClientCredentialsError("Invalid client_id or client_secret_key")

    if not cast(bool, application.is_active):
        raise InvalidClientCredentialsError("Application is inactive")

    if not verify_client_secret_key(
        payload.client_secret_key,
        cast(str, application.client_secret_key_hash),
    ):
        raise InvalidClientCredentialsError("Invalid client_id or client_secret_key")

    return create_access_token(str(application.id), cast(str, application.client_id))
