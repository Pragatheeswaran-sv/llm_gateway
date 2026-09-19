import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

from src.config import settings


class AccessTokenError(ValueError):
    pass


def generate_client_id() -> str:
    return f"msc_{secrets.token_urlsafe(24)}"


def generate_client_secret_key() -> str:
    return secrets.token_urlsafe(48)


def hash_client_secret_key(secret: str) -> str:
    """Return the same HMAC-SHA256 digest for the same secret + pepper."""
    return hmac.new(
        settings.CLIENT_SECRET_HASH_PEPPER.encode("utf-8"),
        secret.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_client_secret_key(secret: str, stored_hash: str) -> bool:
    calculated = hash_client_secret_key(secret)
    return hmac.compare_digest(calculated, stored_hash)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def create_access_token(application_id: str, client_id: str) -> tuple[str, datetime]:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}
    if settings.JWT_ALGORITHM != "HS256":
        raise AccessTokenError("Only HS256 is supported")

    payload = {
        "sub": application_id,
        "client_id": client_id,
        "token_type": "access",
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    header_part = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    token = f"{header_part}.{payload_part}.{_b64url_encode(signature)}"
    return token, expires_at


def decode_access_token(token: str) -> dict:
    try:
        header_part, payload_part, signature_part = token.split(".")
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected_signature = hmac.new(
            settings.JWT_SECRET_KEY.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        actual_signature = _b64url_decode(signature_part)

        if not hmac.compare_digest(expected_signature, actual_signature):
            raise AccessTokenError("Invalid access token")

        header = json.loads(_b64url_decode(header_part))
        payload = json.loads(_b64url_decode(payload_part))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AccessTokenError("Invalid access token") from exc

    if header.get("alg") != "HS256" or payload.get("token_type") != "access":
        raise AccessTokenError("Invalid access token")

    if int(payload.get("exp", 0)) <= int(time.time()):
        raise AccessTokenError("Access token has expired")

    if not payload.get("sub") or not payload.get("client_id"):
        raise AccessTokenError("Invalid access token")

    return payload
