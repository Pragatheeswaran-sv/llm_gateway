import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from src.config import settings


class AccessTokenError(ValueError):
    pass


def clean_dbml(dbml: str) -> str:
    dbml = dbml.strip()

    if dbml.startswith("```"):
        lines = [
            line
            for line in dbml.splitlines()
            if not line.strip().startswith("```")
        ]
        dbml = "\n".join(lines).strip()

    lines = [line.strip() for line in dbml.splitlines() if line.strip()]
    flattened = []
    table_lines = []

    for line in lines:
        table_lines.append(line)
        if line == "}":
            table_name = table_lines[0].rstrip("{").strip()
            columns = table_lines[1:-1]
            flattened.append(f"{table_name} {{{', '.join(columns)}}}")
            table_lines = []

    if table_lines:
        flattened.extend(table_lines)

    dbml = " ".join(flattened).strip()

    return dbml


def validate_dbml(dbml: str) -> bool:
    if not dbml:
        return False

    normalized = clean_dbml(dbml)
    return "Table " in normalized and "{" in normalized and "}" in normalized


def parse_dbml_response(response: str) -> dict[str, str]:
    response = response.strip()
    if "```" in response:
        raise ValueError("Invalid DBML response format from LLM.")

    match = re.fullmatch(
        r"\s*DBML:\s*\n(?P<dbml>.*?)\n\s*SUMMARY:\s*\n"
        r"(?P<summary>.*?)\n\s*EXPLANATION:\s*\n(?P<explanation>.+?)\s*",
        response,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError("Invalid DBML response format from LLM.")

    dbml = clean_dbml(match.group("dbml"))
    summary = match.group("summary").strip()
    explanation = match.group("explanation").strip()

    if not validate_dbml(dbml) or not summary or not explanation:
        raise ValueError("Invalid DBML response from LLM.")

    return {
        "dbml_query": dbml,
        "updated_summary": summary,
        "explanation": explanation,
    }


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
