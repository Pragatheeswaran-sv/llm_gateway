import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.conversation.models import LLMFallbackModel
from src.config import settings
from src.utils.helper import decrypt_api_key

logger = logging.getLogger(__name__)

# Reserve output capacity during the pre-call TPM/TPD check.
DEFAULT_OUTPUT_TOKEN_RESERVE = 800


class NoAvailableLLMError(RuntimeError):
    pass


class ProviderRequestError(RuntimeError):
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


def get_candidate_models(db: Session, estimated_tokens: int) -> list[LLMFallbackModel]:
    now = utcnow()
    models = list(
        db.scalars(
            select(LLMFallbackModel).where(
                LLMFallbackModel.status == "ACTIVE",
            )
        ).all()
    )
    changed = False

    for model in models:
        changed = _reset_windows(model, now) or changed

    if changed:
        db.commit()

    # Select the most capable eligible rows without a model-specific order.
    models.sort(
        key=lambda item: (
            -(item.daily_token_limit - item.used_tokens),
            -(item.tpm_limit - item.minute_tokens),
            -(item.rpm_limit - item.minute_requests),
            item.used_requests,
            item.llm_model,
        )
    )

    eligible: list[LLMFallbackModel] = []
    for model in models:
        ok, reason = _is_eligible(model, estimated_tokens, now)
        if ok:
            eligible.append(model)
        else:
            logger.info(
                "Skipping fallback model=%s reason=%s rpm=%s/%s tpm=%s/%s rpd=%s/%s tpd=%s/%s",
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


def _call_openai_model(
    api_key: str,
    base_url: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
) -> LLMCallResult:
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model_name,
        temperature=0.1,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    content = (response.choices[0].message.content or "").strip()
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0)

    logger.info(
        "LLM success model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
        model_name,
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


def call_model(model: LLMFallbackModel, system_prompt: str, user_prompt: str) -> LLMCallResult:
    if not settings.API_KEY_ENCRYPTION_KEY:
        raise RuntimeError("API_KEY_ENCRYPTION_KEY is not configured")

    api_key = decrypt_api_key(model.api_key, settings.API_KEY_ENCRYPTION_KEY)
    return _call_openai_model(
        api_key=api_key,
        base_url=model.api_base_url,
        model_name=model.llm_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


def call_direct_model(
    model_name: str,
    api_key: str,
    base_url: str,
    system_prompt: str,
    user_prompt: str,
) -> LLMCallResult:
    try:
        return _call_openai_model(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except Exception as exc:
        status_code = _status_code(exc)
        if status_code is not None:
            raise ProviderRequestError(
                f"Provider request failed with HTTP {status_code} for model "
                f"'{model_name}' at '{base_url}': {exc}"
            ) from exc
        raise ProviderRequestError(
            f"Provider request failed for model '{model_name}' at '{base_url}': {exc}"
        ) from exc


def record_success(db: Session, model: LLMFallbackModel, total_tokens: int) -> None:
    now = utcnow()
    _reset_windows(model, now)

    model.used_requests += 1
    model.used_tokens += max(0, total_tokens)
    model.minute_requests += 1
    model.minute_tokens += max(0, total_tokens)
    model.last_used_at = now
    model.is_rate_limited = False
    model.cooldown_until = None
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
