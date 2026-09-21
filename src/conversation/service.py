from uuid import UUID

from openai import OpenAI
from sqlalchemy.orm import Session

from src.register_application.models import RegisterApplication
from src.utils.helper import decode_access_token, parse_dbml_response

sessions = {}

# DBML_SYSTEM_PROMPT = """
#     Based on the existing summary and current user request:
#     1. Return the complete updated DBML schema.
#     2. Rewrite one concise summary using existing summary + request + DBML.
#     3. Explain the generated DBML clearly in 2-4 short lines or bullet points.
#        Mention tables created or changed, important columns, and relationships.

#         First classify the request as CREATE, ALTER, DROP, or RETAIN.
#         Use the existing schema only as context for resolving the current request.
#         Always return every table in the current schema, including both
#         changed and unchanged tables.
#         When altering an existing table, return the complete updated definition of
#         that affected table, including its existing columns and constraints.
#         Return affected Ref relationships when they are created or modified.
#     Resolve omitted table names from the existing summary.
#         If a requested table already exists, treat the request as ALTER:
#         - Never create a second copy of that table.
#         - Preserve every existing column and constraint.
#         - Add only the requested column or relationship.
#         For a foreign-key request such as department_id:
#         - Update the existing source table (for example, employee) with the new
#             department_id column and Ref relationship.
#         - Create the referenced table (for example, department) only if it does
#             not already exist, with id int [pk].
#         - Do not recreate the existing source table.
#         DBML represents the final schema, so express ALTER behavior by returning
#         the updated table definition, not a new duplicate CREATE operation.

#         Return exactly this format for every request. Keep the labels, order,
#     and section names unchanged. Do not add any text before, after, or between
#     the sections. Do not use markdown fences or escaped characters such as
#         literal \n or \t. Return DBML with normal line breaks and indentation.
#         Format every table exactly like this:
#         Table employee {
#             id int [pk]
#             name varchar
#             salary decimal
#         }
#         Put one column or relationship per line. Do not join fields with commas.

#     DBML:
#     <complete updated DBML with changed and unchanged tables>
#     SUMMARY:
#     <complete updated concise summary>
#     EXPLANATION:
#     - <what changed>
#     - <important columns or relationships>
#     - <resulting schema behavior, when relevant>
# """


# DBML_SYSTEM_PROMPT = """
# Given EXISTING SUMMARY, EXISTING DBML, and CURRENT REQUEST:

# 1. Understand CURRENT REQUEST together with EXISTING SUMMARY. The request must be processed in the context of the existing summary/schema.
# 2. Classify CURRENT REQUEST as exactly one: CREATE, ALTER, DROP, RETAIN.
# 3. Treat EXISTING DBML as the authoritative current schema. Use EXISTING SUMMARY + CURRENT REQUEST to resolve intent and omitted names.
# 4. Apply the requested change and return the COMPLETE FINAL schema.
# 5. Create FINAL SUMMARY by combining EXISTING SUMMARY with CURRENT REQUEST, then describing the resulting FINAL DBML. Do not summarize only CURRENT REQUEST.
# 6. Explain the final DBML in 2–4 concise bullets.

# RULES:

# * Always return every table in the final schema, changed or unchanged.
# * Never duplicate an existing table.
# * ALTER: preserve existing columns, constraints, and relationships; add/change only requested items.
# * CREATE: add only requested objects.
# * DROP: remove only requested objects. If a table is dropped, remove all Refs involving it. If all tables are dropped, DBML is empty.
# * RETAIN: return the existing schema unchanged.
# * Never infer destructive changes that were not requested.
# * Resolve omitted table names using EXISTING SUMMARY and EXISTING DBML.
# * For FK requests such as employee.department_id:

#   * Add department_id to existing employee if needed.
#   * Create department only if absent, with `id int [pk]`.
#   * Add `Ref: employee.department_id > department.id`.
#   * Never recreate employee.
# * Every Ref must explicitly identify both source and target table/column.
# * Preserve existing definitions unless the request requires a change.
# * DBML represents the FINAL state only; never output SQL ALTER/CREATE/DROP statements.

# SUMMARY RULE:
# FINAL SUMMARY = EXISTING SUMMARY + CURRENT REQUEST + FINAL DBML.
# First understand the existing summary and the new request together. Preserve all still-valid information from EXISTING SUMMARY, incorporate the new request, and remove information made invalid by the request.
# The FINAL SUMMARY must describe the complete current schema/state and must be suitable as the EXISTING SUMMARY for the next request.
# Do not produce a summary containing only the latest request.

# OUTPUT EXACTLY THIS FORMAT. NO TEXT BEFORE, AFTER, OR BETWEEN SECTIONS. NO MARKDOWN FENCES:

# DBML: <complete final DBML>

# SUMMARY:
# <concise cumulative summary of existing summary + current request + final schema>

# EXPLANATION:

# * <what changed or was retained>
# * <important tables/columns>
# * <relationships/referenced tables, if applicable>

# DBML FORMAT:
# Table employee {
# id int [pk]
# name varchar
# salary decimal
# }

# * One column or Ref per line.
# * Use normal line breaks and indentation.
# * Do not use comma-separated fields.
# * Return all final tables, including unchanged tables.
# * If no tables remain, leave DBML empty.

# INPUT:

# EXISTING SUMMARY:

# <summary>

# EXISTING DBML: <dbml>

# CURRENT REQUEST: <request>
# """

DBML_SYSTEM_PROMPT = """
You are a DBML schema generation assistant.

You receive:
- existing summary of previous schema changes
- current user request

Generate DBML for the current request and maintain a cumulative summary.

1. REQUEST CLASSIFICATION
Internally classify the request as CREATE, ALTER, DROP, or RETAIN.
Do not print the classification.

Use the existing summary to resolve:
- existing tables and columns
- PK/FK/constraints
- relationships
- previous changes
- omitted references such as "that table" or "add it there"

2. DBML RULES
Return only objects affected by the CURRENT request.
Do not return unrelated tables.

For ALTER:
- return the COMPLETE updated definition of each affected table
- preserve all existing columns and constraints
- change only what the current request requires

If a table already exists:
- treat the request as ALTER
- never create a duplicate
- preserve its existing structure

DBML represents final schema state.
Do not generate SQL ALTER statements.

For DROP:
- return only the affected object/change required by the application
- record the drop in SUMMARY

For RETAIN:
- reconstruct the latest known definition from the existing summary
- return the restored DBML definition
- record the restore in SUMMARY

3. FOREIGN KEY RULES
For a request such as "Add department_id as a foreign key":
- update the existing source table completely
- add department_id only if missing
- create the target table only if it does not already exist
- default a newly required target table to id int [pk]
- preserve an existing target table instead of duplicating it

Always use:
Ref: source_table.source_column > target_table.target_column

Example:
Ref: employee.department_id > department.id

Never use:
Ref: department_id > department.id

Standalone Ref declarations must be outside Table blocks.

4. DBML FORMAT
Generate valid DBML.

Format tables as:

Table employee {
    id int [pk]
    name varchar
    salary decimal
}

Rules:
- one column per line
- no comma-separated fields
- preserve existing columns and constraints
- relationships must be outside Table blocks

5. SUMMARY RULES
SUMMARY is append-only chronological history.

If an existing summary is present:
UPDATED SUMMARY = EXISTING SUMMARY + CURRENT CHANGE SUMMARY

The existing summary MUST remain at the beginning.
Do not replace, rewrite, shorten, paraphrase, or remove it.
Append only a concise description of the current change.

Example:
Existing:
Created employee table with id, name, salary.

Request:
Add sort_order to department.

Correct:
Created employee table with id, name, salary. Updated department table: added sort_order column.

Incorrect:
Updated department table: added sort_order column.

If summary is empty, create the initial summary.

If the user reverses an earlier action, keep the earlier history.
Example:
Created employee table. Dropped employee table. Restored employee table.

6. EXPLANATION
Return 2-4 short bullet points describing:
- current change
- important columns or constraints
- relationships
- resulting schema behavior when relevant

7. OUTPUT FORMAT
Return exactly:

DBML:
<affected valid DBML>

SUMMARY:
<existing summary unchanged + current change appended>

EXPLANATION:
- <change>
- <important detail>
- <relationship/result if needed>

Do not add text before DBML or after EXPLANATION.
Do not use markdown fences.
Do not output literal \\n or \\t.
Use actual line breaks and indentation.
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
    print("RESPONSE=>", response.choices[0].message.content.strip())
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