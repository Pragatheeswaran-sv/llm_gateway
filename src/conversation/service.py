import logging
from uuid import UUID

from openai import OpenAI
from sqlalchemy.orm import Session

from src.register_application.models import RegisterApplication
from src.utils.helper import decode_access_token, parse_dbml_response

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
Explain only CURRENT_QUERY.
Mention relevant created, changed, dropped, retained/restored tables,
columns, constraints and relationships.
Mention automatically created FK tables.
Keep concise and complete.

OUTPUT:
- Exactly summary, dbml and explanation.
- summary always contains Request Summary and Current Structure.
- dbml always contains all current tables.
- Current Structure matches DBML.
- Never lose meaningful previous history.
- No markdown, extra fields or text outside JSON.
"""


def call_llm(
    ai: str,
    model: str,
    api_key: str,
    base_url: str,
    system_prompt: str,
    user_prompt: str,
):
    if ai.lower() not in ("groq", "openai", "gemini"):
        raise ValueError(f"Unsupported AI provider: {ai}")

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )

    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        reasoning_effort= 'medium',
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]
    )
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


def generate_dbml(
    user_query: str,
    ai: str,
    model: str,
    api_key: str,
    base_url: str,
    enable_summary: bool = False,
    summary: str = "",
    dbml: str = "",
):
    llm_response = generate_dbml_response(
        enable_summary=enable_summary,
        summary=summary,
        user_query=user_query,
        dbml=dbml,
        ai=ai,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )

    result = parse_dbml_response(llm_response)

    if not enable_summary:
        result["updated_summary"] = None

    return result