import pytest
from fastapi.testclient import TestClient

from src.main import app


client = TestClient(app)

DIRECT_PAYLOAD = {
    "user_query": "Create users",
    "model": "groq",
    "llm": "example-model",
    "llm_api_key": "encrypted-test-key",
    "base_url": "https://example.com/v1",
}


@pytest.mark.parametrize("field", ["model", "llm", "llm_api_key", "base_url"])
@pytest.mark.parametrize("value", [None, "", "   ", 123, [], {}])
def test_invalid_direct_field_names_the_field(field, value):
    response = client.post("/api/v1/generate", json={**DIRECT_PAYLOAD, field: value})
    assert response.status_code == 422
    assert field in response.json()["message"]
    assert response.json()["data"]["error"] == response.json()["message"]
    assert DIRECT_PAYLOAD["llm_api_key"] not in response.text


@pytest.mark.parametrize("field", ["model", "llm", "llm_api_key", "base_url"])
def test_missing_direct_field_names_the_field(field):
    payload = DIRECT_PAYLOAD.copy()
    del payload[field]
    response = client.post("/api/v1/generate", json=payload)
    assert response.status_code == 422
    assert field in response.json()["message"]


@pytest.mark.parametrize("url", [
    "not-a-url", "ftp://example.com", "https://", "https://example.com:bad",
    "[https://example.com](https://example.com)", "https://example.com/a b",
])
def test_invalid_base_url_is_rejected(url):
    response = client.post("/api/v1/generate", json={**DIRECT_PAYLOAD, "base_url": url})
    assert response.status_code == 422
    assert "base_url" in response.json()["message"]


@pytest.mark.parametrize("code, expected", [
    (400, "rejected the request"), (401, "llm_api_key"), (403, "permissions"),
    (404, "llm and base_url"), (422, "request values"), (429, "quota"),
    (500, "server error"),
])
def test_provider_errors_have_summary_and_safe_detail(monkeypatch, code, expected):
    from src.conversation import api, fallback

    monkeypatch.setattr(api, "validate_access_token", lambda *args: None)
    monkeypatch.setattr(fallback.settings, "API_KEY_ENCRYPTION_KEY", "test-only")
    monkeypatch.setattr(fallback, "decrypt_api_key", lambda *args: "decrypted-test-key")

    class ProviderError(Exception):
        status_code = code
        body = {"message": "Rejected encrypted-test-key decrypted-test-key"}

    def reject(**kwargs):
        raise ProviderError()

    def generate(**kwargs):
        return fallback.call_direct_model(
            kwargs["llm"], kwargs["llm_api_key"], kwargs["base_url"], "system", "user"
        )

    monkeypatch.setattr(fallback, "_call_openai_model", reject)
    monkeypatch.setattr(api, "generate_dbml", generate)
    response = client.post(
        "/api/v1/generate", json=DIRECT_PAYLOAD,
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 502
    body = response.json()
    assert set(body) == {"message", "status_code", "data"}
    assert expected in body["message"]
    assert str(code) in body["data"]["error"]
    assert "encrypted-test-key" not in response.text
    assert "decrypted-test-key" not in response.text


def test_health_uses_standard_response_envelope():
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"message", "status_code", "data"}
    assert body["status_code"] == 200
    assert body["data"] == {}


def test_validation_failure_uses_standard_response_envelope():
    response = client.post(
        "/api/v1/generate",
        json={
            "user_query": "Create users",
            "model": "groq",
            "llm": "",
            "llm_api_key": "secret",
            "base_url": "https://api.groq.com/openai/v1",
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"message", "status_code", "data"}
    assert body["status_code"] == 422
    assert "llm" in body["message"]
    assert body["data"]["error"] == body["message"]
    assert set(body["data"]) == {"error"}


def test_partial_provider_error_does_not_echo_payload_details():
    response = client.post(
        "/api/v1/generate",
        json={
            "user_query": "Create users",
            "llm_api_key": "secret-key-must-not-be-returned",
        },
    )

    body = response.json()
    assert response.status_code == 422
    assert set(body) == {"message", "status_code", "data"}
    assert set(body["data"]) == {"error"}
    assert "secret-key-must-not-be-returned" not in str(body)
    assert "model, llm, llm_api_key, and base_url" in body["message"]


def test_unsupported_provider_uses_clean_validation_message():
    response = client.post(
        "/api/v1/generate",
        json={
            "user_query": "Create users",
            "model": "groqs",
            "llm": "openai/gpt-oss-20i",
            "llm_api_key": "encrypted-key",
            "base_url": "https://api.groq.com/openai/v1",
        },
    )

    body = response.json()
    assert response.status_code == 422
    assert set(body) == {"message", "status_code", "data"}
    assert body["message"] == (
        "Unsupported provider 'groqs'. Supported providers: gemini, groq."
    )
    assert body["data"] == {"error": body["message"]}


def test_gemini_provider_is_supported():
    response = client.post(
        "/api/v1/generate",
        json={
            "user_query": "Create users",
            "model": "gemini",
            "llm": "gemini-2.5-flash",
            "llm_api_key": "encrypted-key",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        },
    )

    assert response.status_code != 422
