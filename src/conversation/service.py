from uuid import UUID

from openai import OpenAI
from sqlalchemy.orm import Session

from src.register_application.models import RegisterApplication
from src.utils.helper import decode_access_token, parse_dbml_response

sessions = {}

DBML_SYSTEM_PROMPT = """
    Based on the existing summary and current user request:
    1. Return the complete updated DBML schema.
    2. Rewrite one concise summary using existing summary + request + DBML.
    3. Explain the generated DBML clearly in 2-4 short lines or bullet points.
       Mention tables created or changed, important columns, and relationships.

        First classify the request as CREATE, ALTER, DROP, or RETAIN.
        Use the existing schema only as context for resolving the current request.
        Do not return unaffected tables in DBML.
        When altering an existing table, return the complete updated definition of
        that affected table, including its existing columns and constraints.
        Return affected Ref relationships when they are created or modified.
    Resolve omitted table names from the existing summary.
        If a requested table already exists, treat the request as ALTER:
        - Never create a second copy of that table.
        - Preserve every existing column and constraint.
        - Add only the requested column or relationship.
        For a foreign-key request such as department_id:
        - Update the existing source table (for example, employee) with the new
            department_id column and Ref relationship.
        - Create the referenced table (for example, department) only if it does
            not already exist, with id int [pk].
        - Do not recreate the existing source table.
        DBML represents the final schema, so express ALTER behavior by returning
        the updated table definition, not a new duplicate CREATE operation.

        Return exactly this format for every request. Keep the labels, order,
    and section names unchanged. Do not add any text before, after, or between
    the sections. Do not use markdown fences or escaped characters such as
        literal \n or \t. Return DBML with normal line breaks and indentation.
        Format every table exactly like this:
        Table employee {
            id int [pk]
            name varchar
            salary decimal
        }
        Put one column or relationship per line. Do not join fields with commas.

    DBML:
    <complete updated DBML>
    SUMMARY:
    <complete updated concise summary>
    EXPLANATION:
    - <what changed>
    - <important columns or relationships>
    - <resulting schema behavior, when relevant>
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