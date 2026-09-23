import logging
from uuid import UUID

from sqlalchemy.orm import Session

from src.conversation.fallback import (
    NoAvailableLLMError,
    call_model,
    call_direct_model,
    estimate_tokens,
    get_candidate_models,
    is_auth_or_model_error,
    is_rate_limit_error,
    is_retryable_provider_error,
    record_rate_limit,
    record_success,
    record_unavailable,
)
from src.register_application.models import RegisterApplication
from src.utils.helper import decode_access_token, parse_dbml_response

logger = logging.getLogger(__name__)

# DBML_SYSTEM_PROMPT = """
# You are a DBML schema generation assistant.

# INPUT:
# - EXISTING_SUMMARY: the only memory of earlier schema requests.
# - CURRENT_QUERY: the latest request.
# - SUMMARY_ENABLED: whether an updated summary is required.

# Use EXISTING_SUMMARY to reconstruct the exact prior schema state. Apply only
# CURRENT_QUERY and return the COMPLETE active schema after the change.

# REQUEST TYPES:
# Internally classify CREATE, ALTER, DROP, RETAIN/REVERT. Do not print the type.
# CREATE preserves existing active tables and adds the requested object.
# ALTER preserves every existing table/column/constraint/Ref and changes only
# what was requested. DROP removes only the requested active object and its
# invalid Refs. RETAIN/REVERT restores the last complete dropped definition;
# if a restored table depends on a dropped referenced table, restore that
# required dependency too. Explicit CREATE after DROP creates the newly requested
# schema and must not silently restore old columns.

# FOREIGN KEYS:
# Preserve the complete source table. Add the FK column if missing. If the target
# does not exist, create it with id int [pk]. Use only fully-qualified standalone
# Refs: Ref: source.column > target.column. Ref statements must be outside tables.

# DBML:
# Always return every currently active table, every known current column,
# constraint and valid Ref. Never duplicate a table. One column per line; no
# comma-separated fields.

# SUMMARY MEMORY:
# SUMMARY is the memory for the NEXT request, so it must never lose exact schema
# information. Keep it on ONE physical line with this form:
# Request Summary: <compact chronological actions, including complete last-known
# definitions for dropped objects needed by a future retain>. Current Structure:
# <complete active tables, columns, datatypes, PK/FK/constraints and Refs>.
# When dropping, keep the dropped table's complete last definition in Request
# Summary while removing it from Current Structure. Preserve prior meaningful
# history and append the current action. No newline/tab characters in SUMMARY.

# EXPLANATION:
# Explain only the current request in one concise physical line. No newline/tab.

# OUTPUT:
# Return ONLY valid JSON with exactly:
# {
#   "summary": "...",
#   "dbml": "...",
#   "explanation": "..."
# }
# No markdown fences and no text outside the JSON object.
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
 
CREATE:
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
- No markdown, extra fields or text outside JSON.
"""

def build_user_prompt(enable_summary: bool, summary: str, user_query: str) -> str:
    existing_summary = (summary or "").strip() if enable_summary else ""
    if not existing_summary:
        existing_summary = "NO PREVIOUS SUMMARY"

    return (
        f"EXISTING_SUMMARY:\n{existing_summary}\n\n"
        f"CURRENT_QUERY:\n{user_query.strip()}\n\n"
        f"SUMMARY_ENABLED:\n{enable_summary}"
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


def generate_dbml(
    db: Session,
    user_query: str,
    enable_summary: bool = False,
    summary: str = "",
    direct_model: str | None = None,
    llm: str | None = None,
    llm_api_key: str | None = None,
    base_url: str | None = None,
) -> dict:
    user_prompt = build_user_prompt(
        enable_summary=enable_summary,
        summary=summary,
        user_query=user_query,
    )

    estimated_tokens = estimate_tokens(DBML_SYSTEM_PROMPT + "\n" + user_prompt)

    if direct_model and llm and llm_api_key and base_url:
        call = call_direct_model(
            model_name=llm,
            api_key=llm_api_key,
            base_url=base_url,
            system_prompt=DBML_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        result = parse_dbml_response(call.content)
        if not enable_summary:
            result["updated_summary"] = None
        result["used_tokens"] = call.total_tokens
        return result

    candidates = get_candidate_models(db, estimated_tokens=estimated_tokens)

    if not candidates:
        raise NoAvailableLLMError(
            "No LLM fallback model currently has enough RPM/TPM/RPD/TPD capacity."
        )

    failures: list[str] = []

    for position, model in enumerate(candidates, start=1):
        logger.info(
            "Trying LLM fallback position=%s model=%s estimated_tokens=%s",
            position,
            model.llm_model,
            estimated_tokens,
        )

        try:
            call = call_model(
                model=model,
                system_prompt=DBML_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )

            # Provider usage counts even when parsing/validation fails.
            record_success(db, model, call.total_tokens)

            result = parse_dbml_response(call.content)
            if not enable_summary:
                result["updated_summary"] = None
            result["used_tokens"] = call.total_tokens

            logger.info(
                "LLM selected model=%s fallback_used=%s total_tokens=%s",
                model.llm_model,
                position > 1,
                call.total_tokens,
            )
            return result

        except ValueError as exc:
            # Invalid generated format: try the next generation model.
            failures.append(f"{model.llm_model}: invalid response: {exc}")
            logger.warning("Invalid LLM response model=%s error=%s", model.llm_model, exc)
            continue

        except Exception as exc:
            if is_rate_limit_error(exc):
                record_rate_limit(db, model, exc)
                failures.append(f"{model.llm_model}: rate limited")
                continue

            if is_auth_or_model_error(exc):
                record_unavailable(db, model, str(exc))
                failures.append(f"{model.llm_model}: unavailable")
                continue

            if is_retryable_provider_error(exc):
                failures.append(f"{model.llm_model}: transient provider error: {exc}")
                logger.warning("Transient LLM error model=%s error=%s", model.llm_model, exc)
                continue

            raise

    raise NoAvailableLLMError(
        "All available LLM fallback models failed. " + "; ".join(failures)
    )
