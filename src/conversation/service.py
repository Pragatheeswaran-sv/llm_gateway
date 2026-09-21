from uuid import UUID

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.conversation.models import UserQuery
from src.register_application.models import RegisterApplication
from src.utils.helper import decode_access_token

sessions = {}

SUMMARY_SYSTEM_PROMPT = """
    Maintain the database schema state.

    Always return exactly:

    Tables:
    <table_name>:
        <columns and constraints>

    Dropped tables:
    <table_name>:
        <columns and constraints>

    Keep both sections; use (none) when empty.

    CREATE → add the complete schema to Tables.
    ALTER → update the table and preserve existing schema.
    DROP → move the complete schema to Dropped tables.
    RETAIN → move the complete schema from Dropped tables back to Tables.

    Preserve all columns, keys, constraints, and relationships.
    Return only the updated database state.
"""

DDL_SYSTEM_PROMPT = """
    Generate DDL for the latest user request.

    Use the current database state and recent conversation to resolve missing table names.

    CREATE → create the requested table.
    ALTER → modify the relevant existing table.
    DROP → drop the requested table.
    RETAIN → recreate the requested dropped table from its latest known schema.

    If the user says "the table", "this table", or omits the table name,
    use the table referred to in the current conversation or the most
    recently relevant table.

    For a foreign key:
    - department_id → department(id)
    - manager_id → manager(id)
    - <table>_id → <table>(id) when the relationship is clear.
    - If the referenced table does not exist, create it with id INT PRIMARY KEY first.
    - Then add the foreign-key column and constraint to the source table.

    Return only executable SQL.
"""


def get_session(conversation_id: str):
    if conversation_id not in sessions:
        sessions[conversation_id] = {
            "summary": "",
            "conversation": []
        }

    return sessions[conversation_id]


def call_llm(ai: str, model: str, api_key: str, system_prompt: str, user_prompt: str):
    if ai.lower() == "groq":
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1"
        )
    elif ai.lower() == "openai":
        client = OpenAI(
            api_key=api_key
        )
    else:
        raise ValueError(f"Unsupported AI provider: {ai}")

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

    return response.choices[0].message.content.strip()


def get_recent_conversation(conversation: list, limit: int = 5):
    recent = conversation[-limit:]

    if not recent:
        return "No previous conversation."

    result = []

    for index, item in enumerate(recent, start=1):
        result.append(
            f"""
                Turn {index}
                User: {item["question"]}
                SQL: {item["sql"]}
                """
        )

    return "\n".join(result)


def get_recent_conversation_rows(db: Session, app_id: UUID | str, conversation_id: str, limit: int = 5):
    rows = db.execute(
        select(UserQuery)
        .where(
            UserQuery.app_id == app_id,
            UserQuery.conversation_id == conversation_id,
            UserQuery.is_active.is_(True),
        )
        .order_by(UserQuery.created_at.desc(), UserQuery.message_id.desc())
        .limit(limit)
    ).scalars().all()
    return list(reversed(rows))


def get_latest_summary(db: Session, app_id: UUID | str, conversation_id: str) -> str:
    row = db.execute(
        select(UserQuery)
        .where(
            UserQuery.app_id == app_id,
            UserQuery.conversation_id == conversation_id,
            UserQuery.is_active.is_(True),
        )
        .order_by(UserQuery.created_at.desc(), UserQuery.message_id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.summary if row and row.summary else ""


def get_session_data(db: Session, app_id: UUID | str, conversation_id: str):
    rows = get_recent_conversation_rows(db, app_id, conversation_id, limit=5)
    return {
        "summary": get_latest_summary(db, app_id, conversation_id),
        "conversation": [
            {"question": row.query, "sql": row.summary or ""}
            for row in rows
        ],
    }


def generate_ddl(session: dict, user_query: str, ai: str, model: str, api_key: str):
    recent_conversation = get_recent_conversation(session["conversation"])

    prompt = f"""
        Conversation summary:
        {session["summary"] or "No previous summary."}

        Recent conversation:
        {recent_conversation}

        Latest user request:
        {user_query}

        Generate DDL for the latest user request.
    """

    return call_llm(
        ai=ai,
        model=model,
        api_key=api_key,
        system_prompt=DDL_SYSTEM_PROMPT,
        user_prompt=prompt
    )


def update_summary(session: dict, user_query: str, sql_answer: str, ai: str, model: str, api_key: str):
    prompt = f"""
        Current state:
        {session["summary"] or "Tables:\n  (none)\n\nDropped tables:\n  (none)"}

        Request:
        {user_query}

        SQL:
        {sql_answer}

        Update the state.
        Preserve existing schema during ALTER.
        Preserve complete schema during DROP and RETAIN.
        Return only the updated state in the required format.
    """

    return call_llm(
        ai=ai,
        model=model,
        api_key=api_key,
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        user_prompt=prompt
    )


def clean_sql(sql: str):
    sql = sql.strip()

    if sql.startswith("```"):
        lines = sql.splitlines()

        lines = [
            line
            for line in lines
            if not line.strip().startswith("```")
        ]

        sql = "\n".join(lines).strip()

    return sql


def validate_ddl(sql: str):
    if not sql:
        return False

    sql = clean_sql(sql).upper()

    statements = [
        statement.strip()
        for statement in sql.split(";")
        if statement.strip()
    ]

    if not statements:
        return False

    valid_operations = (
        "CREATE ",
        "ALTER ",
        "DROP "
    )

    return all(
        statement.startswith(valid_operations)
        for statement in statements
    )


def save_conversation(session: dict,user_query: str,sql: str):
    session["conversation"].append({
        "question": user_query,
        "sql": sql
    })


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


def generate_sql(db: Session, app_id: UUID | str, conversation_id: str, user_query: str, ai: str, model: str, api_key: str, external_prompt_id: str | None = None):
    session = get_session_data(db, app_id, conversation_id)

    ddl = generate_ddl(
        session=session,
        user_query=user_query,
        ai=ai,
        model=model,
        api_key=api_key
    )

    ddl = clean_sql(ddl)

    if not validate_ddl(ddl):
        raise ValueError("Invalid DDL generated by LLM.")

    save_conversation(
        session=session,
        user_query=user_query,
        sql=ddl
    )

    summary = update_summary(
        session=session,
        user_query=user_query,
        sql_answer=ddl,
        ai=ai,
        model=model,
        api_key=api_key
    )

    db.add(
        UserQuery(
            app_id=UUID(str(app_id)),
            conversation_id=conversation_id,
            external_prompt_id=external_prompt_id,
            query=user_query,
            summary=summary,
            is_active=True,
            created_by=str(app_id),
            updated_by=str(app_id),
        )
    )
    db.commit()

    return ddl