# LLM Gateway - Registration and Access Token Milestone

## Database

Create the PostgreSQL database first:

```sql
CREATE DATABASE llmservice;
```

Default connection:

```text
postgresql+psycopg://postgres:postgres@localhost:5432/llmservice
```

The service creates one table named `register` with:

- `id`
- `app_name`
- `description` (optional)
- `client_id`
- `client_secret_key` (deterministic HMAC-SHA256 digest, never plaintext)
- `is_active`
- `created_at`

Both endpoints use this same model.

## Secret hashing

The raw `client_secret_key` is returned only at registration time. Before database storage it is converted to deterministic HMAC-SHA256 using `CLIENT_SECRET_HASH_PEPPER`.

The same plaintext secret + the same pepper always produces the same stored digest. Keep the pepper stable and private.

## Endpoint 1 - Register application

`POST /app/register`

Request:

```json
{
  "name": "MY_APP",
  "description": "Optional description"
}
```

Response:

```json
{
  "status": "success",
  "code": 201,
  "data": {
    "client_id": "client_...",
    "client_secret_key": "raw-secret-returned-once",
    "app_name": "MY_APP"
  }
}
```

## Endpoint 2 - Access token

`POST /app/token`

Request:

```json
{
  "client_id": "client_...",
  "client_secret_key": "raw-secret-returned-once"
}
```

The endpoint:

1. Finds the application using `client_id`.
2. Ensures the application is active.
3. Deterministically hashes the submitted secret.
4. Compares it to the stored hash using constant-time comparison.
5. Issues one JWT access token valid for 60 minutes.
6. Does not issue a refresh token.

Response:

```json
{
  "status": "success",
  "code": 200,
  "data": {
    "access_token": "eyJ...",
    "time_expires": "2026-09-19T12:30:00+00:00"
  }
}
```

## Run locally

```bash
poetry install
cp .env.example .env
poetry run uvicorn src.main:app --reload
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

No refresh-token endpoint is implemented in this milestone.
