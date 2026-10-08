import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from openai import APIConnectionError, APITimeoutError, OpenAI
from sqlalchemy import select, Sequence
from sqlalchemy.orm import Session

from src.database import Base
from src.conversation.models import LLMFallbackModel
from src.config import settings

from src.utils.helper import decrypt_api_key

logger = logging.getLogger(__name__)

# Reserve output capacity during the pre-call TPM/TPD check.
DEFAULT_OUTPUT_TOKEN_RESERVE = 300
ACCOUNT_TURN = Sequence("llm_fallback_account_turn", metadata=Base.metadata, start=1)



def _next_account_turn(db: Session) -> int:
    """PostgreSQL nextval allocates a unique turn across processes/workers.

    Sequence increments survive rollback; a cancelled request may consume a turn.
    """
    try:
        return db.scalar(select(ACCOUNT_TURN.next_value())) - 1
    except Exception:
        db.rollback()
        try:
            ACCOUNT_TURN.create(db.get_bind(), checkfirst=True)
            return db.scalar(select(ACCOUNT_TURN.next_value())) - 1
        except Exception as exc:
            logger.warning("Could not allocate next account turn from sequence: %s", exc)
            return 0


class NoAvailableLLMError(RuntimeError):
    pass


class ProviderRequestError(RuntimeError):
    def __init__(
        self,
        error: str,
        message: str = "The LLM provider request failed.",
        status_code: int | None = None,
        response=None,
        retryable: bool = False,
    ):
        super().__init__(error)
        self.message = message
        self.status_code = status_code
        self.response = response
        self.retryable = retryable


class InvalidDirectAPIKeyError(ValueError):
    pass


@dataclass
class LLMCallResult:
    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def estimate_tokens(text: str, output_reserve: int = DEFAULT_OUTPUT_TOKEN_RESERVE) -> int:
    """Cheap provider-independent estimate: about four characters per token + output reserve."""
    input_estimate = max(1, math.ceil(len(text or "") / 4))
    return input_estimate + max(0, output_reserve)


def _reset_windows(model: LLMFallbackModel, now: datetime) -> bool:
    """Reset local minute/day counters when their windows have elapsed."""
    changed = False

    last_used = _aware(model.last_used_at)
    if last_used is not None and last_used.date() < now.date():
        model.used_requests = 0
        model.used_tokens = 0
        changed = True

    window_start = _aware(model.window_start)
    if window_start is None or now - window_start >= timedelta(minutes=1):
        model.minute_requests = 0
        model.minute_tokens = 0
        model.window_start = now
        changed = True

    cooldown = _aware(model.cooldown_until)
    if cooldown is not None and cooldown <= now:
        model.cooldown_until = None
        model.is_rate_limited = False
        changed = True

    return changed


def _is_eligible(model: LLMFallbackModel, estimated_tokens: int, now: datetime) -> tuple[bool, str | None]:
    if (model.status or "").upper() != "ACTIVE":
        return False, "STATUS"

    cooldown = _aware(model.cooldown_until)
    if cooldown is not None and cooldown > now:
        return False, "COOLDOWN"

    if model.is_rate_limited:
        return False, "RATE_LIMITED"

    if model.used_requests >= model.daily_request_limit:
        return False, "RPD_LIMIT"

    if model.used_tokens + estimated_tokens > model.daily_token_limit:
        return False, "TPD_LIMIT"

    if model.minute_requests >= model.rpm_limit:
        return False, "RPM_LIMIT"

    if model.minute_tokens + estimated_tokens > model.tpm_limit:
        return False, "TPM_LIMIT"

    return True, None


def get_candidate_models(
    db: Session,
    estimated_tokens: int,
    skipped_models: list[tuple[LLMFallbackModel, str]] | None = None,
) -> list[LLMFallbackModel]:
    now = utcnow()
    models = list(
        db.scalars(
            select(LLMFallbackModel).where(
                LLMFallbackModel.status == "ACTIVE",
                LLMFallbackModel.is_active.is_(True),
                LLMFallbackModel.priority > 0,
            )
        ).all()
    )
    changed = False

    for model in models:
        changed = _reset_windows(model, now) or changed

    if changed:
        db.commit()

    if not models:
        return []

    # Allocate once per request, before filtering capacity. Database priorities
    # define the entire route order, independent of model names or credentials.
    # An unavailable row falls through to the next priority, wrapping at the end.
    priorities = sorted({model.priority for model in models})
    offset = _next_account_turn(db) % len(priorities)
    priorities = priorities[offset:] + priorities[:offset]
    priority_order = {priority: index for index, priority in enumerate(priorities)}
    models.sort(
        key=lambda item: (
            priority_order[item.priority],
            str(item.id),
        )
    )

    eligible: list[LLMFallbackModel] = []
    for model in models:
        ok, reason = _is_eligible(model, estimated_tokens, now)
        if ok:
            eligible.append(model)
        else:
            if skipped_models is not None:
                skipped_models.append((model, reason or "INELIGIBLE"))
            logger.info(
                "Skipping fallback priority=%s model=%s reason=%s rpm=%s/%s tpm=%s/%s rpd=%s/%s tpd=%s/%s",
                model.priority,
                model.llm_model,
                reason,
                model.minute_requests,
                model.rpm_limit,
                model.minute_tokens,
                model.tpm_limit,
                model.used_requests,
                model.daily_request_limit,
                model.used_tokens,
                model.daily_token_limit,
            )

    return eligible


def call_llm(
    ai: str,
    model: str,
    api_key: str,
    base_url: str,
    system_prompt: str,
    user_prompt: str,
    # decrypt_direct_key: bool = False,
) -> LLMCallResult:
    """Make one OpenAI-compatible provider call and normalize provider errors."""
    from src.conversation.schemas import SUPPORTED_PROVIDERS

    if (ai or "").lower() not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported AI provider: {ai}")
    if not api_key or not api_key.strip():
        raise InvalidDirectAPIKeyError("llm_api_key is required")

    # supplied_api_key = api_key
    if settings.API_KEY_ENCRYPTION_KEY:
        try:
            api_key = decrypt_api_key(api_key, settings.API_KEY_ENCRYPTION_KEY)
        except Exception:
            # Direct callers may supply a provider-native, unencrypted key.
            api_key = api_key

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        content = (
            response.choices[0].message.content or ""
        ).strip() if response.choices else ""
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        logger.info(
            "LLM success provider=%s model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            ai,
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )
        return LLMCallResult(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
    except Exception as exc:
        status_code = _status_code(exc)
        provider_message = _provider_error_message(exc)
        if api_key and len(api_key) > 5:
            provider_message = provider_message.replace(api_key, "[redacted]")
        message = {
            400: "The provider rejected the request. Check the model and base_url.",
            401: "The provider rejected the API key. Check the key and base_url.",
            403: "Provider access was denied. Check API key permissions and model access.",
            404: "The requested model or endpoint is unavailable. Check the model and base_url.",
            422: "The provider could not process the request. Check the model and request values.",
            429: "The provider rate limit or quota was exceeded. Try again later or check your quota.",
        }.get(status_code, "The LLM provider request failed.")
        if isinstance(exc, APITimeoutError):
            message = "The provider request timed out. Check base_url or try again later."
        elif isinstance(exc, APIConnectionError):
            message = "Could not connect to the provider. Check base_url and network connectivity."
        elif status_code is not None and status_code >= 500:
            message = "The provider encountered a server error. Try again later."
        detail = f"Provider HTTP {status_code}: {provider_message}" if status_code is not None else provider_message
        raise ProviderRequestError(
            detail,
            message=message,
            status_code=status_code,
            response=getattr(exc, "response", None),
            retryable=is_retryable_provider_error(exc),
        ) from exc


def call_model(model: LLMFallbackModel, system_prompt: str, user_prompt: str) -> LLMCallResult:
    if not settings.API_KEY_ENCRYPTION_KEY:
        raise RuntimeError("API_KEY_ENCRYPTION_KEY is not configured")

    api_key = decrypt_api_key(model.api_key, settings.API_KEY_ENCRYPTION_KEY)
    return call_llm(
        ai=model.provider,
        model=model.llm_model,
        api_key=api_key,
        base_url=model.api_base_url,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


def _provider_error_message(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if body.get("message"):
            return str(body["message"])

    response = getattr(exc, "response", None)
    response_json = getattr(response, "json", None)
    if callable(response_json):
        try:
            payload = response_json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])

    return str(exc)


def record_success(db: Session, model: LLMFallbackModel, total_tokens: int) -> None:
    now = utcnow()
    _reset_windows(model, now)

    model.used_tokens += max(0, total_tokens)
    model.minute_tokens += max(0, total_tokens)
    model.last_used_at = now
    model.is_rate_limited = False
    model.cooldown_until = None
    db.commit()


def record_attempt(db: Session, model: LLMFallbackModel) -> None:
    """Count a provider call even when it fails before returning token usage."""
    now = utcnow()
    _reset_windows(model, now)
    model.used_requests += 1
    model.minute_requests += 1
    model.last_used_at = now
    db.commit()


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _headers(exc: Exception):
    response = getattr(exc, "response", None)
    return getattr(response, "headers", {}) or {}


def is_rate_limit_error(exc: Exception) -> bool:
    return _status_code(exc) == 429 or exc.__class__.__name__ == "RateLimitError"


def is_auth_or_model_error(exc: Exception) -> bool:
    return _status_code(exc) in {401, 403, 404}


def is_retryable_provider_error(exc: Exception) -> bool:
    if getattr(exc, "retryable", False):
        return True
    code = _status_code(exc)
    if code is not None and (code >= 500 or code in {408, 409, 422, 498}):
        return True
    return exc.__class__.__name__ in {"APITimeoutError", "APIConnectionError"}


def record_rate_limit(db: Session, model: LLMFallbackModel, exc: Exception) -> None:
    now = utcnow()
    headers = _headers(exc)
    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    try:
        seconds = max(1.0, float(retry_after)) if retry_after is not None else 60.0
    except (TypeError, ValueError):
        seconds = 60.0

    model.is_rate_limited = True
    model.cooldown_until = now + timedelta(seconds=seconds)
    model.last_used_at = now
    db.commit()

    logger.warning(
        "Rate limited model=%s cooldown_until=%s",
        model.llm_model,
        model.cooldown_until,
    )


def record_unavailable(db: Session, model: LLMFallbackModel, reason: str) -> None:
    model.status = "UNAVAILABLE"
    model.last_used_at = utcnow()
    db.commit()
    logger.warning("Model marked unavailable model=%s reason=%s", model.llm_model, reason)


def _exception_status_code(exc: Exception) -> int | None:
    status_code = _status_code(exc)
    if status_code is not None:
        return status_code
    import re
    match = re.search(r"Provider HTTP (\d{3})", str(exc))
    return int(match.group(1)) if match else None


def _logged_error(exc: Exception) -> str:
    code = _exception_status_code(exc)
    suffix = f" (HTTP {code})" if code is not None else ""
    detail = str(exc) if isinstance(exc, ProviderRequestError) else _provider_error_message(exc)
    detail = str(detail).strip()
    if len(detail) > 1000:
        detail = detail[:997] + "..."
    return f"{type(exc).__name__}{suffix}: {detail}" if detail else f"{type(exc).__name__}{suffix}"
