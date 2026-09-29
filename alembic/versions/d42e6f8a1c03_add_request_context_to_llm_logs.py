"""Add generate request context and duration to LLM request logs.

Revision ID: d42e6f8a1c03
Revises: c31e5f7a9b20
"""
from alembic import op
import sqlalchemy as sa


revision = "d42e6f8a1c03"
down_revision = "c31e5f7a9b20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "llm_request_logs",
        sa.Column("request_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "llm_request_logs",
        sa.Column("user_prompt", sa.Text(), nullable=True),
    )
    op.add_column(
        "llm_request_logs",
        sa.Column("dbml_query", sa.Text(), nullable=True),
    )
    op.add_column(
        "llm_request_logs",
        sa.Column("summary_enabled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "llm_request_logs",
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "llm_request_logs",
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_llm_request_logs_request_id",
        "llm_request_logs",
        ["request_id"],
    )


def downgrade():
    op.drop_index("ix_llm_request_logs_request_id", table_name="llm_request_logs")
    op.drop_column("llm_request_logs", "duration_ms")
    op.drop_column("llm_request_logs", "summary")
    op.drop_column("llm_request_logs", "summary_enabled")
    op.drop_column("llm_request_logs", "dbml_query")
    op.drop_column("llm_request_logs", "user_prompt")
    op.drop_column("llm_request_logs", "request_id")
