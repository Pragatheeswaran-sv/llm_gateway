# LLM Gateway — NLP to DBML with Server-Side Fallback

This POC exposes a simple `/api/v1/generate` payload while the Python gateway owns Groq model selection, API keys, quota tracking and fallback.

## Public endpoint

```text
POST http://localhost:8000/api/v1/generate
Authorization: Bearer <access_token>
```

First request:

```json
{
  "user_query": "Create an employee table with id, name and salary.",
  "enable_summary": true
}
```

Follow-up request:

```json
{
  "user_query": "Add email column to employee table.",
  "enable_summary": true,
  "summary": "Request Summary: Created employee table with id, name and salary. Current Structure: employee(id int PK, name varchar, salary decimal)."
}
```

The consumer does **not** send provider, model, API key, base URL, DBML state or fallback configuration.

## Response

```json
{
  "message": "DBML generated successfully",
  "status_code": 200,
  "data": {
    "dbml_query": "Table employee {\n  id int [pk]\n  name varchar\n  salary decimal\n}",
    "updated_summary": "Request Summary: Created employee table with id, name and salary. Current Structure: employee(id int PK, name varchar, salary decimal).",
    "explanation": "Created employee table with id as primary key, name and salary.",
    "used_tokens": 1200
  }
}
```

The next consumer request sends the previous `updated_summary` as `summary`. The summary is the conversation/schema memory; the previous DBML is not sent back.

## Fallback flow

Server-side generation order:

```text
GPT-OSS 120B
    ↓ unavailable/limited
Qwen 3.8 27B
    ↓ unavailable/limited
GPT-OSS 20B
    ↓ unavailable/limited
Allam 2 7B
```

For every model row in `llm_fallback_models`, the gateway checks RPM, TPM, RPD, TPD, cooldown and status before making the provider call. Prompt Guard and Safeguard rows are excluded from DBML generation fallback.

A successful provider response updates the request/token counters and returns the provider's `total_tokens` as `used_tokens`.

## Database migration

```bash
poetry run alembic upgrade head
```

The latest migration removes audit columns that were not part of the requested `llm_fallback_models` structure.

## Run API

```bash
poetry install
poetry run uvicorn src.main:app --reload
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

## Tests

Deterministic local tests (no Groq call):

```bash
poetry run pytest tests/test_fallback_router.py tests/test_response_parsing.py -v
```

These cover payload validation, RPM/TPM/RPD/TPD pre-check fallback, real-429 simulation, guard-model exclusion, counter updates and no-model availability.

Live 12-scenario / 74-query integration suite:

```powershell
$env:RUN_LIVE_LLM_TESTS="1"
$env:TEST_API_URL="http://localhost:8000/api/v1/generate"
$env:TEST_ACCESS_TOKEN="<access-token>"
poetry run pytest tests/test_nlp_scenarios.py -v -s
```

See `tests/EXPECTED_SCENARIOS.md` for expected schema behavior and fallback expectations.
