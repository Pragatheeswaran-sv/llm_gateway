import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.conversation.fallback import LLMCallResult, NoAvailableLLMError
from src.conversation.models import LLMFallbackModel
from src.conversation.schemas import DBMLGenerateRequest
from src.conversation import service


GOOD_RESPONSE = json.dumps(
    {
        "summary": (
            "Request Summary: Created employee table. "
            "Current Structure: employee(id int PK, name varchar, salary decimal)."
        ),
        "dbml": "Table employee {\n  id int [pk]\n  name varchar\n  salary decimal\n}",
        "explanation": "Created employee table with id, name and salary.",
    }
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    LLMFallbackModel.__table__.create(engine)
    with Session(engine) as session:
        yield session


def add_model(
    db: Session,
    model: str,
    *,
    rpm_limit: int = 30,
    minute_requests: int = 0,
    tpm_limit: int = 100000,
    minute_tokens: int = 0,
    daily_request_limit: int = 1000,
    used_requests: int = 0,
    daily_token_limit: int = 1000000,
    used_tokens: int = 0,
    status: str = "ACTIVE",
):
    row = LLMFallbackModel(
        provider="Groq",
        llm_model=model,
        api_base_url="https://api.groq.com/openai/v1",
        api_key="test-key",
        daily_token_limit=daily_token_limit,
        used_tokens=used_tokens,
        daily_request_limit=daily_request_limit,
        used_requests=used_requests,
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        minute_requests=minute_requests,
        minute_tokens=minute_tokens,
        window_start=datetime.now(timezone.utc),
        cooldown_until=None,
        is_rate_limited=False,
        status=status,
        last_used_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def fake_success(model, system_prompt, user_prompt):
    return LLMCallResult(
        content=GOOD_RESPONSE,
        prompt_tokens=800,
        completion_tokens=400,
        total_tokens=1200,
    )


def test_public_payload_first_request_has_no_summary():
    request = DBMLGenerateRequest(
        user_query="Create an employee table with id, name and salary.",
        enable_summary=True,
    )
    assert request.summary is None


def test_public_payload_followup_accepts_summary():
    request = DBMLGenerateRequest(
        user_query="Add email column to employee table.",
        enable_summary=True,
        summary="Request Summary: Created employee table. Current Structure: employee(id int PK, name varchar, salary decimal).",
    )
    assert request.summary is not None


def test_complete_direct_provider_fields_are_accepted():
    request = DBMLGenerateRequest(
        user_query="Create employee table.",
        model="groq",
        llm="openai/gpt-oss-20b",
        llm_api_key="secret",
        base_url="https://api.groq.com/openai/v1",
    )

    assert request.model == "groq"
    assert request.llm == "openai/gpt-oss-20b"


@pytest.mark.parametrize(
    "missing_field",
    ["model", "llm", "llm_api_key", "base_url"],
)
def test_partial_direct_provider_fields_are_rejected(missing_field):
    values = {
        "model": "groq",
        "llm": "openai/gpt-oss-20b",
        "llm_api_key": "secret",
        "base_url": "https://api.groq.com/openai/v1",
    }
    values[missing_field] = ""

    with pytest.raises(ValidationError, match=missing_field):
        DBMLGenerateRequest(user_query="Create employee table.", **values)


def test_all_direct_provider_fields_empty_are_accepted_for_fallback():
    request = DBMLGenerateRequest(
        user_query="Create employee table.",
        model="",
        llm="",
        llm_api_key="",
        base_url="",
    )

    assert request.model is None
    assert request.llm is None


def _assert_fallback(db, monkeypatch, primary_kwargs, expected_model="qwen/qwen3.8-27b"):
    add_model(db, "openai/gpt-oss-120b", **primary_kwargs)
    add_model(db, expected_model)
    called = []

    def fake(model, system_prompt, user_prompt):
        called.append(model.llm_model)
        return fake_success(model, system_prompt, user_prompt)

    monkeypatch.setattr(service, "call_model", fake)
    result = service.generate_dbml(
        db=db,
        user_query="Create an employee table with id, name and salary.",
        enable_summary=True,
        summary="",
    )

    assert called == [expected_model]
    assert result["used_tokens"] == 1200
    assert "employee" in result["dbml_query"].lower()


def test_complete_direct_provider_fields_bypass_fallback(db, monkeypatch):
    called = []

    def fake_direct(model_name, api_key, base_url, system_prompt, user_prompt):
        called.append((model_name, api_key, base_url))
        return fake_success(None, system_prompt, user_prompt)

    def fail_fallback(*args, **kwargs):
        raise AssertionError("database fallback should not be called")

    monkeypatch.setattr(service, "call_direct_model", fake_direct)
    monkeypatch.setattr(service, "get_candidate_models", fail_fallback)

    result = service.generate_dbml(
        db=db,
        user_query="Create an employee table.",
        direct_model="groq",
        llm="openai/gpt-oss-20b",
        llm_api_key="secret",
        base_url="https://api.groq.com/openai/v1",
    )

    assert called == [
        ("openai/gpt-oss-20b", "secret", "https://api.groq.com/openai/v1")
    ]
    assert result["used_tokens"] == 1200


def test_rpm_precheck_falls_back(db, monkeypatch):
    _assert_fallback(
        db,
        monkeypatch,
        {"rpm_limit": 30, "minute_requests": 30},
    )


def test_tpm_precheck_falls_back(db, monkeypatch):
    _assert_fallback(
        db,
        monkeypatch,
        {"tpm_limit": 100, "minute_tokens": 0},
    )


def test_rpd_precheck_falls_back(db, monkeypatch):
    _assert_fallback(
        db,
        monkeypatch,
        {"daily_request_limit": 1000, "used_requests": 1000},
    )


def test_tpd_precheck_falls_back(db, monkeypatch):
    _assert_fallback(
        db,
        monkeypatch,
        {"daily_token_limit": 100, "used_tokens": 0},
    )


def test_guard_models_are_not_used_as_generation_fallback(db, monkeypatch):
    add_model(
        db,
        "meta-llama/llama-prompt-guard-2-22m",
        status="GUARD",
    )
    add_model(db, "openai/gpt-oss-20b")
    called = []

    def fake(model, system_prompt, user_prompt):
        called.append(model.llm_model)
        return fake_success(model, system_prompt, user_prompt)

    monkeypatch.setattr(service, "call_model", fake)
    service.generate_dbml(
        db=db,
        user_query="Create employee table with id.",
        enable_summary=True,
    )
    assert called == ["openai/gpt-oss-20b"]


def test_real_429_moves_to_next_model_and_sets_cooldown(db, monkeypatch):
    primary = add_model(db, "openai/gpt-oss-120b")
    add_model(db, "qwen/qwen3.8-27b")
    called = []

    class Response:
        status_code = 429
        headers = {"retry-after": "10"}

    class FakeRateLimitError(Exception):
        status_code = 429
        response = Response()

    def fake(model, system_prompt, user_prompt):
        called.append(model.llm_model)
        if model.llm_model == "openai/gpt-oss-120b":
            raise FakeRateLimitError("rate limited")
        return fake_success(model, system_prompt, user_prompt)

    monkeypatch.setattr(service, "call_model", fake)
    result = service.generate_dbml(
        db=db,
        user_query="Create employee table with id.",
        enable_summary=True,
    )

    db.refresh(primary)
    assert called == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    assert primary.is_rate_limited is True
    assert primary.cooldown_until is not None
    assert result["used_tokens"] == 1200


def test_success_updates_usage_counters(db, monkeypatch):
    row = add_model(db, "openai/gpt-oss-120b", used_requests=5, used_tokens=100)
    monkeypatch.setattr(service, "call_model", fake_success)

    result = service.generate_dbml(
        db=db,
        user_query="Create employee table with id.",
        enable_summary=True,
    )

    db.refresh(row)
    assert row.used_requests == 6
    assert row.used_tokens == 1300
    assert row.minute_requests == 1
    assert row.minute_tokens == 1200
    assert result["used_tokens"] == 1200


def test_no_available_model_returns_controlled_error(db):
    add_model(
        db,
        "openai/gpt-oss-120b",
        daily_request_limit=1,
        used_requests=1,
    )
    with pytest.raises(NoAvailableLLMError):
        service.generate_dbml(
            db=db,
            user_query="Create employee table with id.",
            enable_summary=True,
        )
