from src.conversation.models import LLMFallbackModel, LLMRequestLog
from src.conversation.schemas import SUPPORTED_PROVIDERS
from src.conversation.fallback import (
    _exception_status_code,
    _logged_error,
    InvalidDirectAPIKeyError,
    NoAvailableLLMError,
    ProviderRequestError,
    _status_code,
    call_llm,
    call_model,
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

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.register_application.models import RegisterApplication
from src.utils.helper import decode_access_token, decrypt_api_key, parse_dbml_response
from src.config import settings

logger = logging.getLogger(__name__)

sessions = {}





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


def _start_request_log(
    db: Session,
    *,
    request_id: UUID,
    user_prompt: str,
    summary_enabled: bool,
    summary: str | None,
    is_fallback_mode: bool = False,
) -> LLMRequestLog:
    """Create the single request log row at the start of a request."""
    row = LLMRequestLog(
        request_id=request_id,
        user_prompt=user_prompt,
        summary_enabled=summary_enabled,
        summary=summary,
        is_fallback_mode=is_fallback_mode,
        provider="pending",
        model_name="pending",
        status="started",
        temperature=0.1,
        total_attempts=0,
        failed_attempts=[],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _append_failed_attempt(
    db: Session,
    row: LLMRequestLog,
    *,
    provider: str,
    model_name: str,
    status: str,
    llm_fallback_model_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """Append a failed/skipped attempt to the JSONB array."""
    from datetime import datetime, timezone

    attempt_entry = {
        "provider": provider,
        "model_name": model_name,
        "llm_fallback_model_id": str(llm_fallback_model_id) if llm_fallback_model_id else None,
        "status": status,
        "error_code": error_code,
        "error_message": error_message,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "duration_ms": duration_ms,
        "attempted_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        current = list(row.failed_attempts or [])
        current.append(attempt_entry)
        row.failed_attempts = current
        row.total_attempts = (row.total_attempts or 0) + 1
        db.commit()
    except Exception:
        db.rollback()
        try:
            target = db.query(LLMRequestLog).filter(LLMRequestLog.id == row.id).first()
            if target:
                current = list(target.failed_attempts or [])
                current.append(attempt_entry)
                target.failed_attempts = current
                target.total_attempts = (target.total_attempts or 0) + 1
                db.commit()
        except Exception as log_err:
            logger.warning("Failed to append failed attempt: %s", log_err)
            db.rollback()


def _finish_request_log(
    db: Session,
    row: LLMRequestLog,
    *,
    status: str,
    http_status_code: int | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    provider: str | None = None,
    model_name: str | None = None,
    llm_fallback_model_id=None,
    call=None,
    dbml_query: str | None = None,
) -> None:
    """Finalize the single request log row with the outcome."""
    from datetime import datetime, timezone

    try:
        row.status = status
        if http_status_code is not None:
            row.http_status_code = http_status_code
        if error_message is not None:
            row.error_message = error_message
        if duration_ms is not None:
            row.duration_ms = duration_ms
        row.completed_at = datetime.now(timezone.utc)

        # Fill in the winning attempt details
        if provider:
            row.provider = provider
            row.total_attempts = (row.total_attempts or 0) + 1
        if model_name:
            row.model_name = model_name
        if llm_fallback_model_id is not None:
            row.llm_fallback_model_id = llm_fallback_model_id
        if call:
            row.prompt_tokens = call.prompt_tokens
            row.completion_tokens = call.completion_tokens
            row.total_tokens = call.total_tokens
        if dbml_query is not None:
            row.dbml_query = dbml_query

        db.commit()
    except Exception:
        db.rollback()
        try:
            target = db.query(LLMRequestLog).filter(LLMRequestLog.id == row.id).first()
            if target:
                target.status = status
                if http_status_code is not None:
                    target.http_status_code = http_status_code
                if error_message is not None:
                    target.error_message = error_message
                if duration_ms is not None:
                    target.duration_ms = duration_ms
                target.completed_at = datetime.now(timezone.utc)
                if provider:
                    target.provider = provider
                    target.total_attempts = (target.total_attempts or 0) + 1
                if model_name:
                    target.model_name = model_name
                if llm_fallback_model_id is not None:
                    target.llm_fallback_model_id = llm_fallback_model_id
                if call:
                    target.prompt_tokens = call.prompt_tokens
                    target.completion_tokens = call.completion_tokens
                    target.total_tokens = call.total_tokens
                if dbml_query is not None:
                    target.dbml_query = dbml_query
                db.commit()
        except Exception as log_err:
            logger.warning("Failed to finalize request log: %s", log_err)
            db.rollback()



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
    request_id: UUID | None = None,
    request_log: LLMRequestLog | None = None,
) -> dict:
    user_prompt = build_user_prompt(
        enable_summary=enable_summary,
        summary=summary,
        user_query=user_query,
        dbml=dbml,
    )
    estimated_tokens = estimate_tokens(DBML_SYSTEM_PROMPT + "\n" + user_prompt)

    # Any direct fields select direct mode. Fill omitted configuration from the
    # active fallback model so clients can retain their saved provider settings
    # without sending an API key on every request.
    direct_values_supplied = any(
        value and value.strip()
        for value in (direct_model, llm, llm_api_key, base_url)
    )
    if direct_values_supplied:
        defaults_query = db.query(LLMFallbackModel).filter(
            LLMFallbackModel.is_active.is_(True),
            LLMFallbackModel.status == "ACTIVE",
        )
        if direct_model:
            defaults_query = defaults_query.filter(func.lower(LLMFallbackModel.provider) == direct_model.lower())
        if llm:
            defaults_query = defaults_query.filter(func.lower(LLMFallbackModel.llm_model) == llm.lower())

        defaults = defaults_query.order_by(LLMFallbackModel.priority.asc().nullslast(), LLMFallbackModel.id.asc()).first()
        if defaults is None and not (direct_model and llm and llm_api_key and base_url):
            defaults = db.query(LLMFallbackModel).filter(
                LLMFallbackModel.is_active.is_(True),
                LLMFallbackModel.status == "ACTIVE",
            ).order_by(LLMFallbackModel.priority.asc().nullslast(), LLMFallbackModel.id.asc()).first()

        provider = direct_model or (defaults.provider if defaults else None)
        model_name = llm or (defaults.llm_model if defaults else None)
        resolved_base_url = base_url or (defaults.api_base_url if defaults else None)
        if llm_api_key:
            resolved_api_key = llm_api_key
        elif defaults is not None:
            if not settings.API_KEY_ENCRYPTION_KEY:
                raise ValueError("API_KEY_ENCRYPTION_KEY is not configured")
            try:
                resolved_api_key = decrypt_api_key(
                    defaults.api_key, settings.API_KEY_ENCRYPTION_KEY
                )
            except Exception as exc:
                logger.exception("Failed to decrypt the configured LLM API key")
                raise ValueError("Invalid configured API key format") from exc

        if not provider or not model_name or not resolved_api_key or not resolved_base_url:
            raise NoAvailableLLMError(
                "Direct LLM settings are incomplete and no active configured model can fill the missing values."
            )
        if provider.lower() not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported AI provider: {provider}")

        # Check if the requested provider/model is already present in llm_fallback_models table
        matched_fallback_model = None
        if provider and model_name:
            candidates_query = db.query(LLMFallbackModel).filter(
                func.lower(LLMFallbackModel.provider) == provider.lower(),
                func.lower(LLMFallbackModel.llm_model) == model_name.lower(),
                LLMFallbackModel.is_active.is_(True),
            )
            all_matches = candidates_query.all()
            if all_matches:
                if resolved_api_key and settings.API_KEY_ENCRYPTION_KEY:
                    for m in all_matches:
                        try:
                            dec = decrypt_api_key(m.api_key, settings.API_KEY_ENCRYPTION_KEY)
                            if dec == resolved_api_key or m.api_key == resolved_api_key:
                                matched_fallback_model = m
                                break
                        except Exception:
                            if m.api_key == resolved_api_key:
                                matched_fallback_model = m
                                break
                if matched_fallback_model is None:
                    matched_fallback_model = all_matches[0]

        call = None
        try:
            call = call_llm(
                ai=provider,
                model=model_name,
                api_key=resolved_api_key,
                base_url=resolved_base_url,
                system_prompt=DBML_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
        except InvalidDirectAPIKeyError as exc:
            if request_log:
                _append_failed_attempt(
                    db, request_log,
                    provider=provider, model_name=model_name,
                    status="failed", error_code="INVALID_API_KEY",
                    error_message=str(exc),
                    llm_fallback_model_id=matched_fallback_model.id if matched_fallback_model else None,
                )
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            code = _exception_status_code(exc)
            category = (
                "rate_limited" if is_rate_limit_error(exc)
                else "service_unavailable" if code == 503
                else "unavailable" if is_auth_or_model_error(exc)
                else "provider_error"
            )
            if request_log:
                _append_failed_attempt(
                    db, request_log,
                    provider=provider, model_name=model_name,
                    status=category,
                    error_code=(
                        "RATE_LIMITED" if category == "rate_limited"
                        else f"HTTP_{code}" if code is not None
                        else "MODEL_UNAVAILABLE" if category == "unavailable"
                        else "PROVIDER_ERROR"
                    ),
                    error_message=_logged_error(exc),
                    llm_fallback_model_id=matched_fallback_model.id if matched_fallback_model else None,
                )
            raise
        try:
            result = parse_dbml_response(call.content, include_intent=True)
            intent = result.pop("intent")

            if intent != "SCHEMA":
                result["dbml_query"] = ""
                result["updated_summary"] = summary
        except Exception as exc:
            if request_log:
                _append_failed_attempt(
                    db, request_log,
                    provider=provider, model_name=model_name,
                    status=(
                        "rate_limited" if _exception_status_code(exc) == 429
                        else "service_unavailable" if _exception_status_code(exc) == 503
                        else "invalid_response" if call is not None and isinstance(exc, ValueError)
                        else "failed"
                    ),
                    error_code=(
                        "RATE_LIMITED" if _exception_status_code(exc) == 429
                        else f"HTTP_{_exception_status_code(exc)}"
                        if _exception_status_code(exc) is not None
                        else None
                    ),
                    error_message=_logged_error(exc),
                    prompt_tokens=call.prompt_tokens if call else None,
                    completion_tokens=call.completion_tokens if call else None,
                    total_tokens=call.total_tokens if call else None,
                    llm_fallback_model_id=matched_fallback_model.id if matched_fallback_model else None,
                )
            raise

        # Success — finalize the request log with the winning attempt
        if request_log:
            _finish_request_log(
                db, request_log,
                status="success",
                http_status_code=200,
                provider=provider,
                model_name=model_name,
                llm_fallback_model_id=matched_fallback_model.id if matched_fallback_model else None,
                call=call,
                dbml_query=result.get("dbml_query"),
            )
        if not enable_summary:
            result["updated_summary"] = None
        result["token_used"] = call.total_tokens
        return result

    # ── Fallback mode ──
    skipped_models: list[tuple[LLMFallbackModel, str]] = []
    candidates = get_candidate_models(
        db, estimated_tokens=estimated_tokens, skipped_models=skipped_models
    )

    # Log skipped models into the JSONB array
    for model, reason in skipped_models:
        if request_log:
            _append_failed_attempt(
                db, request_log,
                provider=model.provider, model_name=model.llm_model,
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
                llm_fallback_model_id=model.id,
            )

    if not candidates:
        raise NoAvailableLLMError("No active fallback models are available.")

    failures: list[str] = []
    for position, model in enumerate(candidates, start=1):
        record_attempt(db, model)
        call = None
        try:
            call = call_model(model=model, system_prompt=DBML_SYSTEM_PROMPT, user_prompt=user_prompt)
        except Exception as exc:
            logger.warning("Failed to generate DBML response: %s", _logged_error(exc))
            code = _exception_status_code(exc)
            category = (
                "rate_limited" if is_rate_limit_error(exc)
                else "service_unavailable" if code == 503
                else "unavailable" if is_auth_or_model_error(exc)
                else "provider_error"
            )
            if request_log:
                _append_failed_attempt(
                    db, request_log,
                    provider=model.provider, model_name=model.llm_model,
                    status=category,
                    error_code=(
                        "RATE_LIMITED" if category == "rate_limited"
                        else f"HTTP_{code}" if code is not None
                        else "MODEL_UNAVAILABLE" if category == "unavailable"
                        else "PROVIDER_ERROR"
                    ),
                    error_message=_logged_error(exc),
                    llm_fallback_model_id=model.id,
                )
            if is_rate_limit_error(exc):
                record_rate_limit(db, model, exc)
            elif is_auth_or_model_error(exc):
                record_unavailable(db, model, _logged_error(exc))
            if is_rate_limit_error(exc) or is_auth_or_model_error(exc) or code == 503 or is_retryable_provider_error(exc):
                failures.append(f"{model.llm_model}: {_logged_error(exc)}")
                continue
            raise

        record_success(db, model, call.total_tokens)
        try:
            result = parse_dbml_response(call.content)
        except ValueError as exc:
            if request_log:
                _append_failed_attempt(
                    db, request_log,
                    provider=model.provider, model_name=model.llm_model,
                    status="invalid_response",
                    error_code="INVALID_RESPONSE",
                    error_message=type(exc).__name__,
                    prompt_tokens=call.prompt_tokens,
                    completion_tokens=call.completion_tokens,
                    total_tokens=call.total_tokens,
                    llm_fallback_model_id=model.id,
                )
            failures.append(f"{model.llm_model}: invalid response")
            continue

        # Success — finalize with the winning model's details
        if request_log:
            _finish_request_log(
                db, request_log,
                status="success",
                http_status_code=200,
                provider=model.provider,
                model_name=model.llm_model,
                llm_fallback_model_id=model.id,
                call=call,
                dbml_query=result.get("dbml_query"),
            )
        if not enable_summary:
            result["updated_summary"] = None
        result["token_used"] = call.total_tokens
        return result

    raise NoAvailableLLMError(
        "All available LLM fallback models failed. " + "; ".join(failures)
    )



DBML_SYSTEM_PROMPT = """
You convert database requests into DBML and maintain schema state. Output ONLY one raw JSON object (no markdown fences, no text outside it) with exactly these keys, in this order:
{"intent":"SCHEMA|RELATED|GREETING|FAREWELL|ACKNOWLEDGEMENT|UNRELATED","dbml":"...","summary":"...","explanation":"..."}

OUTPUT CONTRACT (highest priority, applies to every intent, including clarification, no-op, greeting, farewell, acknowledgement and unrelated)
- ALWAYS return all four keys: intent, dbml, summary, explanation. Never omit a key, never stop early, never return only part of the object.
- The response must start with { and end with "} after the explanation text. The explanation is the LAST key, and the JSON must be closed.
- The summary must ALWAYS contain all 5 sections, in this order, each with its heading: Request Summary:, Available Tables:, Available Groups:, Available References:, Pending Rename:. An empty section is "- None". Never stop after 1-4 sections and never cut a section in the middle.
- Empty values are still present: dbml = "" when not applicable. Never drop the key.
- Write in this order: intent, then dbml, then the complete summary (all 5 sections), then explanation.
- If you are running low on space, shorten the Request Summary bullets and the explanation wording. Never drop a key, a summary section, a table definition, or the closing of the JSON.
- Keep every string on a single line using \\n escapes (no literal line breaks).
- Fixed summary skeleton to fill (copy these headings exactly):
  "Request Summary:\\n- ...\\n\\nAvailable Tables:\\n- ...\\n\\nAvailable Groups:\\n- ...\\n\\nAvailable References:\\n- ...\\n\\nPending Rename:\\n- ..."

INPUTS
- EXISTING_SUMMARY: history and ledgers.
- EXISTING_DBML: active schema, source of truth.
- CURRENT_QUERY: apply only this request.
- PRESERVATION RULE: Preserve all existing tables, columns, references, and summary records from previous turns. Never omit existing active schema unless explicitly requested with DROP.

INTENT (mixed requests: prioritize supported schema changes)
- SCHEMA: table/index/group CREATE, ALTER (ADD/REMOVE/MODIFY/RENAME columns), DROP, RETAIN, REVERT, RENAME. Also pasted DBML in the query (treat as create/merge request).
- RELATED: database/schema question, no change.
- GREETING / FAREWELL / ACKNOWLEDGEMENT: as named.
- UNRELATED: off-topic or unsupported SQL (SELECT, INSERT, UPDATE, DELETE, TRUNCATE, MERGE, GRANT, REVOKE, CREATE VIEW/PROCEDURE/FUNCTION/TRIGGER/DATABASE/USER). Never generate these.

DBML OUTPUT
- Every SCHEMA request has exactly one outcome: APPLIED, NO-OP or CLARIFICATION.
- APPLIED (the change is made): dbml = complete active schema including ALL previously created tables plus new/modified ones. NEVER return only the newly touched table. If no ACTIVE table remains (for example the last table was dropped), dbml = "".
- NO-OP: the request is clear but cannot be applied or is already satisfied (target table missing or DROPPED, ALTER/DROP on a table that is not ACTIVE, RETAIN of a table that is already ACTIVE). Change nothing, keep intent SCHEMA, set dbml = the complete active schema UNCHANGED (never ""), copy the actual existing summary unchanged, and say why nothing changed in the explanation.
- CLARIFICATION: if the schema request is unclear, ambiguous, incomplete, conflicting or invalid, OR needs a follow-up question (including CREATE of an existing ACTIVE table, or CREATE of a DROPPED name with no columns named), apply NO change, keep intent SCHEMA, set dbml = "" (empty string, never the existing schema), copy the actual existing summary unchanged, and ask the question in explanation.
- All other intents: dbml = "".
- LINE BREAKS: dbml is ONE single-line JSON string. Use the escaped \\n for every line break and \\n\\n between blocks. Never use literal line breaks or tabs. Columns are indented with two spaces.
- ORDER: all Table blocks (indexes inside them) -> all Ref lines -> all TableGroup blocks. Refs go after the tables, never inline or inside a Table block.
- LAYOUT: Table blocks are separated by one blank line. After the last Table block comes one blank line, then all Ref lines, one per line, with NO blank line between Refs. No trailing blank line. Order is stable: existing tables, columns and Refs keep their position; new or retained tables go after the last Table block; new columns go after the last column of their table; new Refs go after the existing Refs.
- REFS ACCUMULATE: the Ref list in dbml = every ACTIVE ref already in EXISTING_DBML and Available References, PLUS any new ref from CURRENT_QUERY. Copy all existing Ref lines forward unchanged. Never output only the new ref. Remove a ref only when its table is dropped, its column is removed, or the user asks to remove it (a rename updates it).
- REF CREATION (only these add a Ref):
  a) CREATE where the query names a column ending in _id and the table named by that prefix is ACTIVE with an id primary key: add Ref: <new_table>.<column> > <prefix_table>.id in the same response, without a separate request. Only columns named in that CREATE query trigger this. If the prefix table is missing or DROPPED, create the column only, add no Ref, and never create that table.
  b) An explicit request to add or make a foreign key/reference (one or several columns, self-references allowed): add one Ref per requested column, in the order requested. If the source column is missing, add it as int. If a referenced table is missing, create it with ONLY id int [pk] and no other columns, after the existing tables in the order mentioned. If the referenced table is DROPPED, it is a NO-OP (suggest RETAIN). Never duplicate an existing Ref.
  c) NEVER add a Ref for: ALTER ADD column (even if the name ends with _id and that table exists), columns you inferred, a recreated table whose old Refs are DROPPED (they are not revived; only an explicit request adds them back).
- Ref format: Ref: <source_table>.<fk_column> > <target_table>.<pk_column>
- Index format inside Table: indexes { (<column>) [unique, name: '<index_name>'] (<column_a>, <column_b>) [name: '<index_name>'] }
- Group format: TableGroup <group_name> { <table_a> <table_b> }
- PK: id int [pk] unless another PK is given. Keep explicit and existing types exactly as given. Never use string/integer.
- DATA TYPES: a) column named by the user without a type: identifier, count or quantity (name is id or ends with _id) = int; a monetary value = decimal; a calendar date (name is date or ends with _date) = date; everything else = varchar. b) columns YOU infer because the user named none: ONLY int and varchar. c) an explicit type is always kept exactly.
- Include only ACTIVE tables/refs/groups. Indexes must use existing columns. Preserve everything unaffected.

SUMMARY (required for EVERY intent; exactly these 5 sections, this order, same headings, no others)
Request Summary:
- <consolidated summary of the whole conversation so far, or None>
Available Tables:
- <table_name>: <complete exact definition on ONE line, columns separated by spaces, e.g. id int [pk] <column> <type>; include indexes>; Status: ACTIVE|DROPPED
Available Groups:
- <group_name>: Members: <table_a, table_b>; Status: ACTIVE|DROPPED
Available References:
- <source_table.fk_column > target_table.pk_column>; Status: ACTIVE|DROPPED
Pending Rename:
- Old Name: <old>; New Name: <new>; Status: PENDING
- Summary is ONE single-line JSON string: heading lines and "- " items separated by \\n, sections separated by a blank line (\\n\\n).
- Empty section = "- None". Never truncate/shorten definitions or memberships. Never log unapplied, invalid or ambiguous operations as completed. Summary, ledgers and DBML must agree.
- Available Tables & References keep ALL existing entries and append new ones. Never overwrite or drop existing active tables from the summary unless DROPPED.
- Available References keeps ALL existing entries and adds new ones; never replace the list with only the new ref. Refs of a dropped table stay as DROPPED; restoring or explicitly re-adding one makes it ACTIVE (never two entries for the same ref).
- Each table name has at most ONE ledger entry: a recreated table overwrites its old entry, and dropping again overwrites the old DROPPED definition (the latest dropped definition is what RETAIN restores).
- REQUEST SUMMARY IS SUMMARIZED, NOT LOGGED: rewrite it every turn by merging the Request Summary in EXISTING_SUMMARY with the completed result of CURRENT_QUERY. Do NOT copy the user's wording, do NOT add one line per request, and do NOT just append to the old text.
  a) Write 2-4 short "- " bullets in your own words, grouped by object and outcome (tables created and how they relate, changes made, current state, dropped/renamed items).
  b) Combine related steps into one statement (a table created and later linked to another = one bullet). Replace superseded steps with their final outcome (a column added then removed is not mentioned; a table created then dropped is stated as dropped).
  c) State what is currently ACTIVE and what is DROPPED or PENDING, so the bullets match the ledgers.
  d) Include only completed operations. Clarifications, no-ops, invalid or unapplied requests are not recorded.
  e) A pasted-DBML query is described in words as the tables and relationships it added, never copied.
- Keep all DROPPED entries in the ledgers (needed for RETAIN/REVERT); the Request Summary bullets may stay short because the ledgers hold the exact definitions.
- Non-SCHEMA intents, NO-OP and clarification responses: copy the actual existing summary text unchanged in the same format. NEVER output literal placeholder text like '<copy the existing summary>'—always output the real text.
- Existing-table CREATE is not logged.

TABLE RULES
- CREATE: if the name is ACTIVE, change nothing, return dbml = "", and ask whether to add columns or modify. If the name is DROPPED and the query names columns, create a NEW table with those columns (it replaces the dropped definition). If the name is DROPPED and no columns are named, clarify: retain the dropped table or create a new one?
- CREATE columns:
  a) Query names columns: create the table with exactly those columns, in the order given, with types per DATA TYPES. Add nothing extra, except id int [pk] when no primary key is specified. Apply REF CREATION a).
  b) Query names no columns: create id int [pk] plus 3-6 relatable columns describing the table's own attributes, inferred from the table name and purpose, typed int or varchar only.
  c) Inferred columns (rule b) must be self-contained attributes of the table itself. NEVER infer foreign-key columns, columns named after or pointing to another table (any <other_table>_id style column), or Ref statements, even if other tables exist.
  Clarify only if the table name is truly ambiguous.
- ALTER: change only the requested columns/properties/refs. The table must be ACTIVE, otherwise NO-OP. ADD appends the column (type per DATA TYPES) and adds no Ref (see REF CREATION c). REMOVE deletes the column and its Refs (Refs become DROPPED in the ledger).
- DROP: drop every table named in the request (several allowed). Remove each table and its refs and any group membership from DBML; keep the exact definition in the summary as DROPPED. Columns in other tables that pointed to it stay as plain columns. A table that is not ACTIVE is a NO-OP.
- RETAIN: if the named table is already ACTIVE, NO-OP. Otherwise restore its latest dropped definition exactly as saved (columns, indexes) as ACTIVE, placed after the last Table block. Also restore every DROPPED table that has a DROPPED Ref pointing to the retained table (its dependents), each after it with its saved definition. Then restore the DROPPED Refs that involve a restored table and whose other table is ACTIVE, placed after the existing Refs. Never create tables the ledger does not hold and never touch unrelated schema. REVERT: restore the previous state without losing unrelated changes.
- RENAME: if the source is ACTIVE and the new name is clear and unused, rename immediately without asking. Update refs, indexes, group members and ledgers, preserving properties. If the source is missing/inactive, the target is used by an ACTIVE table, or a name is missing/ambiguous: change nothing, return dbml = "", and ask; record the known pair under Pending Rename. Clear it on completion or cancellation.

INDEX RULES
- CREATE/ALTER/DROP only on ACTIVE tables with existing columns. Never invent columns or tables, never duplicate indexes. DROP removes only the index. Update the table definition in the ledger. Missing table/column is a NO-OP (unchanged dbml); ambiguous target: clarify with dbml = "", no change.

GROUP RULES
- CREATE only from existing ACTIVE tables. Default name: <table>_group. ADD/REMOVE change membership only. DROP removes only the group, never tables/refs. Never create tables for groups. If members are missing/inactive, do not create the group, treat it as a NO-OP (unchanged dbml), and name the unavailable tables. Update Available Groups; the DBML must reflect it.

EXPLANATION (plain text, no DBML/code)
- SCHEMA and RELATED: exactly 5 lines , each adding new information. SCHEMA: operation, affected objects, columns/types, keys, refs, indexes, memberships, resulting status, what is preserved. For CREATE, state whether columns were user-specified or inferred, and name every Ref added automatically. NO-OP: state why nothing changed (not active, already active, missing) and offer the fitting next step (create, retain or add). Clarifications: state what is unclear or missing, state that no change was made, and ask one specific question; never claim unapplied changes. DROP: confirm the definition is saved for RETAIN. RELATED: a useful answer to the question.
- GREETING: warm; if a schema exists, mention it and ask what next ("Hello! We were working on your schema. What would you like to do next?"); otherwise "Hello! How can I help you?"
- FAREWELL: brief goodbye. ACKNOWLEDGEMENT: "You're welcome!" or similar.
- UNRELATED: State that you only handle database schema operations, and politely guide them back. For example: "I am specifically designed for database schema operations. Would you like to work on your schema?" (If the schema is currently empty, suggest: "Shall we start by creating a new table?")

RESPONSE EXAMPLE (follow this exact format; earlier turns created table_a, table_b, table_c and linked table_a and table_b to table_c; request: drop table_c)
{"intent":"SCHEMA","dbml":"Table table_a {\\n  id int [pk]\\n  col_a varchar\\n  fk_col int\\n}\\n\\nTable table_b {\\n  id int [pk]\\n  col_a varchar\\n  fk_col int\\n}","summary":"Request Summary:\\n- table_a and table_b were created and linked to table_c through fk_col.\\n- table_c was dropped, so table_a and table_b remain active without foreign keys and the links are marked DROPPED.\\n\\nAvailable Tables:\\n- table_a: id int [pk] col_a varchar fk_col int; Status: ACTIVE\\n- table_b: id int [pk] col_a varchar fk_col int; Status: ACTIVE\\n- table_c: id int [pk] col_a varchar; Status: DROPPED\\n\\nAvailable Groups:\\n- None\\n\\nAvailable References:\\n- table_a.fk_col > table_c.id; Status: DROPPED\\n- table_b.fk_col > table_c.id; Status: DROPPED\\n\\nPending Rename:\\n- None","explanation":"1. Dropped table_c from the active schema.\\n2. Removed refs table_a.fk_col > table_c.id and table_b.fk_col > table_c.id.\\n3. table_a and table_b stay active and keep their fk_col int columns without foreign keys.\\n4. No tables were created or renamed and no groups were affected.\\n5. The table_c definition and its refs are saved in the summary for RETAIN or REVERT."}

LAYOUT EXAMPLE (dbml value only, with refs; blank line before the first Ref, Refs on consecutive lines)
"Table table_a {\\n  id int [pk]\\n  col_a varchar\\n}\\n\\nTable table_b {\\n  id int [pk]\\n  table_a_id int\\n  col_x int\\n}\\n\\nTable table_c {\\n  id int [pk]\\n}\\n\\nRef: table_b.table_a_id > table_a.id\\nRef: table_b.col_x > table_c.id"

CLARIFICATION EXAMPLE (unclear request: "change it"; same schema state as above, so copy the real existing summary and dbml is empty)
{"intent":"SCHEMA","dbml":"","summary":"Request Summary:\\n- table_a and table_b were created and linked to table_c through fk_col.\\n\\nAvailable Tables:\\n- table_a: id int [pk] col_a varchar fk_col int; Status: ACTIVE\\n- table_b: id int [pk] col_a varchar fk_col int; Status: ACTIVE\\n\\nAvailable Groups:\\n- None\\n\\nAvailable References:\\n- None\\n\\nPending Rename:\\n- None","explanation":"1. The request does not say which table or column to change.\\n2. It also does not say what the change should be.\\n3. No change was made to the schema, refs, indexes or groups.\\n4. The existing tables, refs and history are preserved as they were.\\n5. Which table and column do you want to change, and what should the change be?"}

NO-OP EXAMPLE (clear request to add a column to a table that is not ACTIVE; dbml is the unchanged complete active schema, copy the real existing summary)
{"intent":"SCHEMA","dbml":"Table table_a {\\n  id int [pk]\\n  col_a varchar\\n}","summary":"Request Summary:\\n- table_a was created.\\n\\nAvailable Tables:\\n- table_a: id int [pk] col_a varchar; Status: ACTIVE\\n\\nAvailable Groups:\\n- None\\n\\nAvailable References:\\n- None\\n\\nPending Rename:\\n- None","explanation":"1. The requested table is not active in the current schema.\\n2. Nothing was added because the change needs an active table.\\n3. The schema, refs, indexes and groups are unchanged.\\n4. The summary and history are preserved as they were.\\n5. Would you like to create the table, or retain it if it was dropped?"}

BEFORE REPLYING CHECK: valid single JSON object with 4 keys only (intent, dbml, summary, explanation), all present and in that order, closed with "} at the end, no literal line breaks; correct intent; summary contains all 5 sections in order (Request Summary, Available Tables, Available Groups, Available References, Pending Rename), none cut off or missing, separated by blank lines; explanation present and complete; Request Summary is a consolidated 2-4 bullet summary in your own words (not one line per request, not copied user wording); outcome is correct: APPLIED = complete dbml with ALL active tables (never only the latest table), NO-OP = unchanged complete dbml, CLARIFICATION = dbml "" with a question and no change; all existing tables from previous turns must remain in dbml and Available Tables; never output literal placeholder text like '<copy the existing summary>'; dbml layout: tables, one blank line, Refs one per line, stable order, new or retained tables last; ALL existing ACTIVE refs kept plus new ones, in both dbml and Available References; a Ref is added only by REF CREATION a) or b), never by ALTER ADD or inferred columns; DROP removes the table and its refs and keeps other tables' columns; RETAIN restores the latest dropped definition, its dependents and its refs, and is a NO-OP if already ACTIVE; types follow DATA TYPES (inferred columns int/varchar only); SCHEMA/RELATED explanation is 5 numbered lines; no duplicates of ACTIVE tables; state consistent across dbml, summary and ledgers.
"""



