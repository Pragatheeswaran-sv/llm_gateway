# Implementation Summary

## Registration/token

- `POST /app/register` creates an application and returns client credentials.
- `POST /app/token` validates client credentials and returns a short-lived access token.
- `/api/v1/generate` requires `Authorization: Bearer <access_token>`.

## Generate payload

The public payload is intentionally small:

```json
{
  "user_query": "Create an employee table with id, name and salary.",
  "enable_summary": true,
  "summary": "optional only after the first request"
}
```

The gateway, not the consumer, owns model/API-key/base-URL selection.

## Summary memory

The first request has no summary. Every successful response returns `updated_summary`; the consumer sends that value on the next turn. The summary contains a compact request history and the complete current schema state, including enough last-known dropped definitions for RETAIN/REVERT.

## Fallback model routing

`llm_fallback_models` stores exactly the requested model and quota fields. Generation fallback order is server-owned and currently prefers GPT-OSS 120B → Qwen 3.8 27B → GPT-OSS 20B → Allam 2 7B. Prompt Guard/Safeguard rows are not generation fallbacks.

Pre-call checks use local RPM/TPM/RPD/TPD counters. Real HTTP 429 responses set `is_rate_limited` and `cooldown_until`. Authentication/model errors can mark a model unavailable. Successful calls update counters and expose provider `total_tokens` as `used_tokens` in the response.

## Tests

- `test_fallback_router.py`: deterministic fallback and payload tests.
- `test_response_parsing.py`: single-line summary/explanation and multiline DBML parsing.
- `test_nlp_scenarios.py`: optional live 12-scenario / 74-query endpoint suite.
- `EXPECTED_SCENARIOS.md`: expected schema and fallback behavior.
