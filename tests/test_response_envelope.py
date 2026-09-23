from fastapi.testclient import TestClient

from src.main import app


client = TestClient(app)


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