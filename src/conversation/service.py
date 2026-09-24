from src.conversation.models import LLMFallbackModel, LLMRequestLog
from src.conversation.fallback import (
    DEFAULT_TEMPERATURE,
    NoAvailableLLMError,
    ProviderRequestError,
    call_model,
    call_direct_model,
    estimate_tokens,
    get_candidate_models,
    is_auth_or_model_error,
    is_rate_limit_error,
    is_retryable_provider_error,
    _provider_error_message,
    record_attempt,
    record_rate_limit,
    record_success,
    record_unavailable,
)
import logging
from uuid import UUID

from openai import OpenAI
from sqlalchemy.orm import Session

from src.register_application.models import RegisterApplication
from src.utils.helper import decode_access_token, decrypt_api_key, parse_dbml_response
from src.config import settings

logger = logging.getLogger(__name__)

sessions = {}

# DBML_SYSTEM_PROMPT = """
# You are a database schema assistant.

# INPUT:
# - EXISTING SUMMARY: summary of previous conversation and database requests.
# - CURRENT QUERY: the user's latest database request.

# Your task is to return exactly three outputs:
# 1. SUMMARY
# 2. DBML
# 3. EXPLANATION

# GENERAL RULE:
# Use the existing context and apply ONLY the current query.
# The DBML must contain ALL current tables and represent the complete database
# schema after applying the current query.

# 1. SUMMARY
# - Combine the existing summary with the current query.
# - Preserve important previous requests and context.
# - Add the current request to the summary.
# - Summarize user intent and requested database operations.
# - Do not represent the summary as the database schema.
# - Do not remove previous context unless the current query explicitly
#   changes or reverses it.
# - Keep the summary concise but sufficient for future queries.

# 2. DBML
# - Generate valid DBML based on the existing context and current query.
# - Apply only the change requested in the current query.
# - ALWAYS return ALL current tables in the DBML.
# - Preserve every existing table, column, primary key, foreign key,
#   constraint, and relationship unless the current query explicitly
#   changes or removes it.
# - Never return only the affected table.
# - Never remove existing schema because it was not mentioned in the
#   current query.
# - Never duplicate an existing table.

# CREATE:
# - Create the requested table with all requested columns and constraints.
# - Preserve all existing tables.

# ALTER:
# - Find the existing table from the context.
# - Preserve its complete existing schema.
# - Add or modify only what the current query requests.
# - Preserve all other tables unchanged.
# - Do not rebuild the table using only fields mentioned in the current query.

# DROP:
# - Remove the requested table from the active DBML.
# - Remove relationships involving the dropped table.
# - Preserve all unrelated tables and relationships.
# - IMPORTANT: A dropped table is still part of the conversation context.
# - Preserve enough information about the dropped table to allow a later
#   RETAIN request to restore its COMPLETE previous schema.

# RETAIN:
# - Identify the table requested for retention from the existing context.
# - If the table was previously dropped, restore it to the DBML.
# - Restore the COMPLETE schema the table had immediately before it was dropped.
# - Restore ALL of its previous columns.
# - Restore its primary keys.
# - Restore its foreign-key columns and constraints.
# - Restore its valid relationships.
# - Do NOT recreate the table using only information mentioned in the
#   current RETAIN request.
# - Do NOT create a simplified version of the table.
# - Do NOT lose columns that were present before DROP.
# - Do NOT create duplicate copies of the table.
# - After restoring the table, include it together with ALL other current
#   tables in the DBML.
# - Restore relationships only when their referenced/source tables exist.
# - If a relationship cannot currently be restored because its referenced
#   table does not exist, preserve the table and omit only that invalid
#   relationship.

# FOREIGN KEY:
# - Identify the source table, foreign-key column, and referenced table.
# - Add the foreign-key column to the source table.
# - If the SOURCE table does not exist, create it with ONLY:
#     id int [pk]
#   Then add the requested foreign-key column.
# - If the REFERENCED table does not exist, create it with ONLY:
#     id int [pk]
# - Do not add any other columns to a newly created table unless the
#   current query explicitly requests them.
# - Both source and referenced tables must exist in the DBML.
# - Add the relationship as a separate Ref statement at the end of DBML.
# - Use this format:
#   Ref: <source_table>.<foreign_key_column> > <target_table>.<target_column>
# - Do not use [ref: > ...] inside the column definition.
# - Preserve all existing foreign keys and relationships.

# REFERENCES:
# - Resolve phrases such as "the table", "this table", "that table",
#   "previous table", or omitted table names using the existing context
#   and current query.
# - If the current query refers to an existing or previously dropped table,
#   identify it from the context.
# - Do not invent unrelated tables or schema.

# DBML COMPLETENESS:
# - ALWAYS return every current table.
# - Preserve every existing table that has not been dropped.
# - Preserve every existing column.
# - Preserve every existing primary key and constraint.
# - Preserve every valid relationship.
# - Include every table required by a new foreign key.
# - Include every table restored by RETAIN.
# - Include every requested change.
# - Only changes requested by the current query may modify the schema.

# 3. EXPLANATION
# - Explain the changes made for the CURRENT QUERY only.
# - Clearly describe:
#   - tables created, modified, dropped, or retained
#   - columns added, modified, or removed
#   - keys or constraints added or changed
#   - foreign-key relationships created, modified, or removed
#   - supporting tables created because they were required
# - For RETAIN:
#   - identify the restored table
#   - explain that its previous complete schema was restored
#   - mention the restored columns, keys, constraints, and relationships
#   - explain any relationship that could not be restored and why
# - If a missing table was created for a foreign-key operation, explain
#   that it was created with only id as the primary key.
# - Do not describe unrelated previous changes.
# - Do not simply repeat the DBML.
# - Explain the current change clearly and in sufficient detail for a
#   developer to understand what happened.

# OUTPUT FORMAT:
# Return ONLY valid JSON in exactly this structure:

# {
#   "summary": "<updated conversation summary>",
#   "dbml": "<complete DBML containing ALL current tables>",
#   "explanation": "<detailed explanation of the current query change>"
# }

# Do not return markdown fences.
# Do not return SQL.
# Do not return additional fields.
# Do not return any text outside the JSON object.
# """

# DBML_SYSTEM_PROMPT = """
# You are a database schema assistant.

# INPUT:
# - EXISTING_SUMMARY: previous request history and schema context.
# - EXISTING_DBML: exact current database schema.
# - CURRENT_QUERY: latest user request.

# Use EXISTING_SUMMARY only as context/history.
# Use EXISTING_DBML as the exact schema source of truth.
# Apply only CURRENT_QUERY.

# Return ONLY:
# {
#   "summary": "Request Summary: ...\\nCurrent Structure: ...",
#   "dbml": "...",
#   "explanation": "..."
# }

# SUMMARY:
# Rebuild the summary from EXISTING_SUMMARY + CURRENT_QUERY after every request.

# Request Summary:
# - Preserve all meaningful previous actions and schema information.
# - Append the current request/result.
# - Preserve CREATE, ALTER, DROP, RETAIN, tables, columns, constraints and relationships.

# Current Structure:
# - Describe the COMPLETE resulting schema.
# - Include every current table, column, important constraint and relationship.
# - It must match the resulting DBML.
# - Include tables automatically created by the current request.
# - Do not include dropped tables.

# Keep the summary concise without losing meaningful information.

# DBML:
# Always return the COMPLETE resulting schema.
# Preserve every unaffected table, column, key, constraint and relationship.
# Never duplicate tables.

# CREATE:
# Add the requested table/columns and preserve existing schema.

# ALTER:
# Change only what CURRENT_QUERY requests.
# Preserve the complete affected table and all unrelated schema.

# DROP:
# Remove ONLY the requested table(s).
# Remove their constraints and Ref relationships.
# Preserve every unrelated table unchanged.
# If any table remains, DBML MUST contain those tables.
# Return empty DBML only when no tables remain.
# Keep the DROP action in Request Summary.

# RETAIN / REVERT:
# Restore the requested dropped/reverted table using its most recent complete
# definition available in context.
# Restore columns, keys, constraints, FK columns and valid relationships.
# Preserve all other current tables.
# Record the action in Request Summary.

# FOREIGN KEY / REFERENCE:
# Add the FK column if missing.
# If source table does not exist, create it with:
# id int [pk]
# If target table does not exist, create it with:
# id int [pk]
# Include automatically created tables in DBML and Current Structure.
# Add:
# Ref: source_table.source_column > target_table.target_column
# Put Ref statements after all Table blocks.
# Never put Ref inside a Table block or use inline [ref].
# Do not create unrelated columns.

# REFERENCES:
# Resolve "this", "that", "the table", "those tables", and similar references
# using EXISTING_DBML first, then EXISTING_SUMMARY.

# EXPLANATION:
# Explain only CURRENT_QUERY.
# Mention relevant created, changed, dropped, retained/restored tables,
# columns, constraints and relationships.
# Mention automatically created FK tables.
# Keep concise and complete.

# OUTPUT:
# - Exactly summary, dbml and explanation.
# - summary always contains Request Summary and Current Structure.
# - dbml always contains all current tables.
# - Current Structure matches DBML.
# - Never lose meaningful previous history.
# - No markdown, extra fields or text outside JSON.
# """

DBML_SYSTEM_PROMPT = """
You are a database schema assistant.



INPUT:
- EXISTING_SUMMARY: previous request history and schema context.
- EXISTING_DBML: exact current database schema.
- CURRENT_QUERY: latest user request.



Use EXISTING_SUMMARY only as context/history.
Use EXISTING_DBML as the exact schema source of truth.
Apply only CURRENT_QUERY.



Return ONLY:
{
  "summary": "Request Summary: ...\\nCurrent Structure: ...",
  "dbml": "...",
  "explanation": "..."
}



SUMMARY:
Rebuild the summary from EXISTING_SUMMARY + CURRENT_QUERY after every request.



Request Summary:
- Preserve all meaningful previous actions and schema information.
- Append the current request/result.
- Preserve CREATE, ALTER, DROP, RETAIN, tables, columns, constraints and relationships.



Current Structure:
- Describe the COMPLETE resulting schema.
- Include every current table, column, important constraint and relationship.
- It must match the resulting DBML.
- Include tables automatically created by the current request.
- Do not include dropped tables.



Keep the summary concise without losing meaningful information.



DBML:
Always return the COMPLETE resulting schema.
Preserve every unaffected table, column, key, constraint and relationship.
Never duplicate tables.



TYPES:
- Infer types from the column meaning: text -> `varchar`; IDs, counts, and foreign keys -> `int`.
- Money fields (salary, price, amount, balance, discount) -> `decimal`; date fields -> `date`.
- Honor an explicitly requested type. Never use `string` or `integer`.



SCOPE:
- Only process CREATE, ALTER, DROP, REVERT, or RETAIN requests.
- Treat ADD and REMOVE as ALTER requests.
- For irrelevant input, leave DBML and summary unchanged and set explanation to: "Irrelevant to the conversation."



CREATE:
Use `Table` and `Ref:` exactly. Never use lowercase `table` or `ref`.
Add the requested table/columns and preserve existing schema.



ALTER:
Change only what CURRENT_QUERY requests.
Preserve the complete affected table and all unrelated schema.



DROP:
Remove ONLY the requested table(s).
Remove their constraints and Ref relationships.
Preserve every unrelated table unchanged.
If any table remains, DBML MUST contain those tables.
Return empty DBML only when no tables remain.
Keep the DROP action in Request Summary.



RETAIN / REVERT:
Restore the requested dropped/reverted table using its most recent complete
definition available in context.
Restore columns, keys, constraints, FK columns and valid relationships.
Preserve all other current tables.
Record the action in Request Summary.



FOREIGN KEY / REFERENCE:
Add the FK column if missing.
If source table does not exist, create it with:
id int [pk]
If target table does not exist, create it with:
id int [pk]
Include automatically created tables in DBML and Current Structure.
Add:
Ref: source_table.source_column > target_table.target_column
Put Ref statements after all Table blocks.
Never put Ref inside a Table block or use inline [ref].
Do not create unrelated columns.



REFERENCES:
Resolve "this", "that", "the table", "those tables", and similar references
using EXISTING_DBML first, then EXISTING_SUMMARY.



EXPLANATION:
Explain only the actual change made by CURRENT_QUERY in 1-2 detailed sentences.
Mention the affected table, columns, constraints, relationships, and automatically
created tables only when directly related to CURRENT_QUERY.
Do not mention previous requests, existing schema, unchanged objects, summary,
history, or unrelated changes



OUTPUT:
- Exactly summary, dbml and explanation.
- summary always contains Request Summary and Current Structure.
- dbml always contains all current tables.
- Current Structure matches DBML.
- Never lose meaningful previous history.
- No markdown, extra fields or text outside JSON."""
 

def call_llm(
    ai: str,
    model: str,
    api_key: str,
    base_url: str,
    system_prompt: str,
    user_prompt: str,
):
    if ai.lower() not in ("groq", "openai", "gemini", "claude", "kimi",):
        raise ValueError(f"Unsupported AI provider: {ai}")

    try:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )

        response = client.chat.completions.create(
            model=model,
            temperature=DEFAULT_TEMPERATURE,
            reasoning_effort='medium',
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        )
    except Exception as exc:
        logger.exception("LLM request failed for provider %s", ai)
        raise RuntimeError("LLM request failed") from exc

    if not response.choices or not response.choices[0].message.content:
        raise RuntimeError("LLM returned an empty response")

    logger.info("response: %s", response.choices[0].message.content.strip())
    logger.info("Prompt tokens: %s", response.usage.prompt_tokens)
    logger.info("Completion tokens: %s", response.usage.completion_tokens)
    logger.info("Total tokens: %s", response.usage.total_tokens)

    return response.choices[0].message.content.strip()


def generate_dbml_response(
    enable_summary: bool,
    summary: str,
    user_query: str,
    dbml: str,
    ai: str,
    model: str,
    api_key: str,
    base_url: str,
):
    prompt = f"""
        Existing summary:
        {summary if enable_summary and summary else "Summary disabled."}

        Current DBML:
        {dbml or "No existing DBML."}

        User request:
        {user_query}

        Summary enabled: {enable_summary}
    """

    return call_llm(
        ai=ai,
        model=model,
        api_key=api_key,
        base_url=base_url,
        system_prompt=DBML_SYSTEM_PROMPT,
        user_prompt=prompt
    )


def validate_access_token(db: Session, token: str) -> RegisterApplication:
    payload = decode_access_token(token)
    app_id = payload.get("sub")
    client_id = payload.get("client_id")

    if not app_id or not client_id:
        raise ValueError("Invalid access token")

    try:
        app_uuid = UUID(str(app_id))
    except ValueError as exc:
        raise ValueError("Invalid access token") from exc

    app = db.get(RegisterApplication, app_uuid)
    if app is None or not app.is_active or app.client_id != client_id:
        raise ValueError("Access token is invalid or application is inactive")

    return app



def build_user_prompt(enable_summary: bool, summary: str, user_query: str, dbml: str = "") -> str:
    existing_summary = (summary or "").strip() if enable_summary else ""
    if not existing_summary:
        existing_summary = "NO PREVIOUS SUMMARY"

    return (
        f"EXISTING_SUMMARY:\n{existing_summary}\n\n"
        f"EXISTING_DBML:\n{dbml or 'No existing DBML.'}\n\n"
        f"CURRENT_QUERY:\n{user_query.strip()}\n\n"
        f"SUMMARY_ENABLED:\n{enable_summary}"
    )


def _start_attempt(
    db: Session,
    *,
    provider: str,
    model_name: str,
    fallback_model: LLMFallbackModel | None,
    estimated_tokens: int | None,
) -> LLMRequestLog:
    row = LLMRequestLog(
        llm_fallback_model_id=fallback_model.id if fallback_model else None,
        provider=provider,
        model_name=model_name,
        status="started",
        temperature=DEFAULT_TEMPERATURE,
        estimated_tokens=estimated_tokens,
        used_tokens=fallback_model.used_tokens if fallback_model else None,
        used_requests=fallback_model.used_requests if fallback_model else None,
        minute_requests=fallback_model.minute_requests if fallback_model else None,
        minute_tokens=fallback_model.minute_tokens if fallback_model else None,
    )
    db.add(row)
    # Persist before calling the provider so failed attempts remain auditable.
    db.commit()
    db.refresh(row)
    return row


def _finish_attempt(
    db: Session,
    row: LLMRequestLog,
    *,
    status: str,
    call=None,
    http_status_code: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    fallback_model: LLMFallbackModel | None = None,
) -> None:
    from datetime import datetime, timezone

    row.status = status
    row.http_status_code = http_status_code
    row.error_code = error_code
    row.error_message = error_message
    row.completed_at = datetime.now(timezone.utc)
    row.prompt_tokens = call.prompt_tokens if call else None
    row.completion_tokens = call.completion_tokens if call else None
    row.total_tokens = call.total_tokens if call else None
    if fallback_model is not None:
        row.used_tokens = fallback_model.used_tokens
        row.used_requests = fallback_model.used_requests
        row.minute_requests = fallback_model.minute_requests
        row.minute_tokens = fallback_model.minute_tokens
    db.commit()


def _exception_status_code(exc: Exception) -> int | None:
    from src.conversation.fallback import _status_code

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


def generate_dbml(
    db: Session,
    user_query: str,
    enable_summary: bool = False,
    summary: str = "",
    direct_model: str | None = None,
    llm: str | None = None,
    llm_api_key: str | None = None,
    base_url: str | None = None,
    dbml: str = "",
) -> dict:
    user_prompt = build_user_prompt(
        enable_summary=enable_summary,
        summary=summary,
        user_query=user_query,
        dbml=dbml,
    )
    estimated_tokens = estimate_tokens(DBML_SYSTEM_PROMPT + "\n" + user_prompt)

    if direct_model and llm and llm_api_key and base_url:
        attempt = _start_attempt(
            db,
            provider=direct_model,
            model_name=llm,
            fallback_model=None,
            estimated_tokens=estimated_tokens,
        )
        call = None
        try:
            call = call_direct_model(
                model_name=llm,
                api_key=llm_api_key,
                base_url=base_url,
                system_prompt=DBML_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            result = parse_dbml_response(call.content)
        except Exception as exc:
            _finish_attempt(
                db,
                attempt,
                status=(
                    "rate_limited" if _exception_status_code(exc) == 429
                    else "service_unavailable" if _exception_status_code(exc) == 503
                    else "invalid_response" if call is not None and isinstance(exc, ValueError)
                    else "failed"
                ),
                call=call,
                http_status_code=_exception_status_code(exc),
                error_code=(
                    "RATE_LIMITED" if _exception_status_code(exc) == 429
                    else f"HTTP_{_exception_status_code(exc)}"
                    if _exception_status_code(exc) is not None
                    else None
                ),
                error_message=_logged_error(exc),
            )
            raise
        _finish_attempt(db, attempt, status="success", call=call)
        if not enable_summary:
            result["updated_summary"] = None
        result["token_used"] = call.total_tokens
        return result

    skipped_models: list[tuple[LLMFallbackModel, str]] = []
    candidates = get_candidate_models(
        db, estimated_tokens=estimated_tokens, skipped_models=skipped_models
    )
    skipped_logs: list[tuple[LLMRequestLog, LLMFallbackModel, str]] = []
    for model, reason in skipped_models:
        skipped_log = _start_attempt(
            db,
            provider=model.provider,
            model_name=model.llm_model,
            fallback_model=model,
            estimated_tokens=estimated_tokens,
        )
        skipped_logs.append((skipped_log, model, reason))
        _finish_attempt(
            db,
            skipped_log,
            status="skipped_capacity",
            error_code=reason,
            error_message=(
                f"Skipped by capacity/availability check: {reason}; "
                f"minute_requests={model.minute_requests}/{model.rpm_limit}, "
                f"minute_tokens={model.minute_tokens}/{model.tpm_limit}, "
                f"used_requests={model.used_requests}/{model.daily_request_limit}, "
                f"used_tokens={model.used_tokens}/{model.daily_token_limit}, "
                f"estimated_tokens={estimated_tokens}"
            ),
            fallback_model=model,
        )
    if not candidates:
        for skipped_log, model, reason in skipped_logs:
            _finish_attempt(
                db,
                skipped_log,
                status="service_unavailable",
                http_status_code=503,
                error_code=reason,
                error_message=(
                    f"Request returned HTTP 503; candidate skipped: {reason}; "
                    f"minute_requests={model.minute_requests}/{model.rpm_limit}, "
                    f"minute_tokens={model.minute_tokens}/{model.tpm_limit}, "
                    f"used_requests={model.used_requests}/{model.daily_request_limit}, "
                    f"used_tokens={model.used_tokens}/{model.daily_token_limit}, "
                    f"estimated_tokens={estimated_tokens}"
                ),
                fallback_model=model,
            )
        if skipped_models:
            reasons = "; ".join(
                f"{model.provider}/{model.llm_model}: {reason} "
                f"(minute_requests={model.minute_requests}/{model.rpm_limit}, "
                f"minute_tokens={model.minute_tokens}/{model.tpm_limit}, "
                f"used_requests={model.used_requests}/{model.daily_request_limit}, "
                f"used_tokens={model.used_tokens}/{model.daily_token_limit}, "
                f"estimated_tokens={estimated_tokens})"
                for model, reason in skipped_models
            )
            message = (
                "No fallback provider was called because every configured model "
                f"failed local eligibility checks: {reasons}"
            )
        else:
            message = "No active fallback models are configured; no provider was called."
        raise NoAvailableLLMError(message)

    failures: list[str] = []
    for position, model in enumerate(candidates, start=1):
        logger.info(
            "Trying LLM fallback position=%s priority=%s model=%s estimated_tokens=%s",
            position,
            model.priority,
            model.llm_model,
            estimated_tokens,
        )
        attempt = _start_attempt(
            db,
            provider=model.provider,
            model_name=model.llm_model,
            fallback_model=model,
            estimated_tokens=estimated_tokens,
        )
        record_attempt(db, model)
        call = None
        try:
            call = call_model(model=model, system_prompt=DBML_SYSTEM_PROMPT, user_prompt=user_prompt)
        except Exception as exc:
            code = _exception_status_code(exc)
            category = (
                "rate_limited" if is_rate_limit_error(exc)
                else "service_unavailable" if code == 503
                else "unavailable" if is_auth_or_model_error(exc)
                else "provider_error"
            )
            _finish_attempt(
                db, attempt, status=category, http_status_code=code,
                error_code=(
                    "RATE_LIMITED" if category == "rate_limited"
                    else f"HTTP_{code}" if code is not None
                    else "MODEL_UNAVAILABLE" if category == "unavailable"
                    else "PROVIDER_ERROR"
                ),
                error_message=_logged_error(exc), fallback_model=model,
            )
            if is_rate_limit_error(exc):
                record_rate_limit(db, model, exc)
                failures.append(f"{model.llm_model}: {_logged_error(exc)}")
                continue
            if is_auth_or_model_error(exc):
                record_unavailable(db, model, _logged_error(exc))
                failures.append(f"{model.llm_model}: {_logged_error(exc)}")
                continue
            if category == "service_unavailable":
                failures.append(f"{model.llm_model}: {_logged_error(exc)}")
                logger.warning("LLM service unavailable model=%s error=%s", model.llm_model, _logged_error(exc))
                continue
            if is_retryable_provider_error(exc):
                failures.append(f"{model.llm_model}: {_logged_error(exc)}")
                logger.warning("Transient LLM error model=%s error=%s", model.llm_model, _logged_error(exc))
                continue
            raise

        # Provider usage is counted even if its returned content cannot be parsed.
        record_success(db, model, call.total_tokens)
        try:
            result = parse_dbml_response(call.content)
        except ValueError as exc:
            _finish_attempt(
                db, attempt, status="invalid_response", call=call,
                error_code="INVALID_RESPONSE", error_message=type(exc).__name__, fallback_model=model,
            )
            failures.append(f"{model.llm_model}: invalid response")
            continue

        _finish_attempt(db, attempt, status="success", call=call, fallback_model=model)
        if not enable_summary:
            result["updated_summary"] = None
        result["token_used"] = call.total_tokens
        logger.info(
            "LLM selected priority=%s model=%s fallback_used=%s total_tokens=%s",
            model.priority, model.llm_model, position > 1, call.total_tokens,
        )
        return result

    raise NoAvailableLLMError(
        "All available LLM fallback models failed. " + "; ".join(failures)
    )
