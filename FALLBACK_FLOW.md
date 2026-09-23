# Server-Side LLM Fallback Flow

## Consumer contract

First call:

```json
{
  "user_query": "Create an employee table with id, name and salary.",
  "enable_summary": true
}
```

Later call:

```json
{
  "user_query": "Add department_id as a foreign key.",
  "enable_summary": true,
  "summary": "<previous updated_summary>"
}
```

The consumer never supplies model, provider, API key, base URL, current DBML, quota or fallback state.

## Internal flow

```text
POST /api/v1/generate
        ↓
validate bearer token
        ↓
build prompt from user_query + optional summary
        ↓
estimate input + output-reserve tokens
        ↓
load llm_fallback_models
        ↓
reset expired minute/day windows
        ↓
order generation candidates
        ↓
for each candidate:
    status ACTIVE?
    cooldown expired?
    RPM available?
    projected TPM available?
    RPD available?
    projected TPD available?
        ↓ yes
    call provider
        ├─ success → count actual tokens → parse → return
        ├─ 429 → rate-limit/cooldown row → next model
        ├─ 401/403/404 → mark UNAVAILABLE → next model
        ├─ timeout/5xx/498 → next model
        └─ invalid generated payload → count used tokens → next model
        ↓
no usable model
        ↓
HTTP 503
```

## Balance formulas

```text
RPM remaining = rpm_limit - minute_requests
TPM remaining = tpm_limit - minute_tokens
RPD remaining = daily_request_limit - used_requests
TPD remaining = daily_token_limit - used_tokens
```

A model is skipped when the projected request would exceed any applicable balance.

## Generation fallback order

```text
openai/gpt-oss-120b
    ↓
qwen/qwen3.8-27b
    ↓
openai/gpt-oss-20b
    ↓
allam-2-7b
```

Prompt Guard and GPT-OSS Safeguard rows are stored with `status=GUARD` and are not used as DBML generators.

## Response

```json
{
  "message": "DBML generated successfully",
  "status_code": 200,
  "data": {
    "dbml_query": "...",
    "updated_summary": "...",
    "explanation": "...",
    "used_tokens": 1200
  }
}
```

The service returns only the selected call's actual provider `total_tokens`. Model selection/fallback details stay internal and are logged server-side.
