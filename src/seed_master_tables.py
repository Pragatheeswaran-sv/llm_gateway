"""Bootstrap database objects for the current gateway version.

There are no AI/model master tables in this milestone yet. Running this module
creates the current SQLAlchemy tables and is intentionally idempotent.
"""

from src.database import init_db


if __name__ == "__main__":
    init_db()
    print("Database tables are ready.")
