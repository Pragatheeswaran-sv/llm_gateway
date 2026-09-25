"""Store per-model usage and capacity skip reasons in LLM request logs.

Revision ID: c31e5f7a9b20
Revises: b20d3e4f5a61
"""
from alembic import op
import sqlalchemy as sa


revision = "c31e5f7a9b20"
down_revision = "b20d3e4f5a61"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        "uq_llm_request_logs_request_attempt",
        "llm_request_logs",
        type_="unique",
    )
    op.drop_index("ix_llm_request_logs_request_id", table_name="llm_request_logs")
    op.drop_column("llm_request_logs", "request_id")
    op.drop_column("llm_request_logs", "attempt_no")
    op.drop_column("llm_request_logs", "daily_token_limit")
    op.drop_column("llm_request_logs", "daily_request_limit")
    op.drop_column("llm_request_logs", "rpm_limit")
    op.drop_column("llm_request_logs", "tpm_limit")
    op.drop_column("llm_request_logs", "created_by")
    op.drop_column("llm_request_logs", "updated_by")
    op.add_column("llm_request_logs", sa.Column("error_code", sa.String(40), nullable=True))
    op.add_column("llm_request_logs", sa.Column("used_tokens", sa.BigInteger(), nullable=True))
    op.add_column("llm_request_logs", sa.Column("used_requests", sa.Integer(), nullable=True))
    op.add_column("llm_request_logs", sa.Column("minute_requests", sa.Integer(), nullable=True))
    op.add_column("llm_request_logs", sa.Column("minute_tokens", sa.BigInteger(), nullable=True))


def downgrade():
    op.drop_column("llm_request_logs", "minute_tokens")
    op.drop_column("llm_request_logs", "minute_requests")
    op.drop_column("llm_request_logs", "used_requests")
    op.drop_column("llm_request_logs", "used_tokens")
    op.drop_column("llm_request_logs", "error_code")
    op.add_column("llm_request_logs", sa.Column("updated_by", sa.String(255), nullable=True))
    op.add_column("llm_request_logs", sa.Column("created_by", sa.String(255), nullable=True))
    op.add_column("llm_request_logs", sa.Column("tpm_limit", sa.BigInteger(), nullable=True))
    op.add_column("llm_request_logs", sa.Column("rpm_limit", sa.Integer(), nullable=True))
    op.add_column("llm_request_logs", sa.Column("daily_request_limit", sa.Integer(), nullable=True))
    op.add_column("llm_request_logs", sa.Column("daily_token_limit", sa.BigInteger(), nullable=True))
    op.add_column("llm_request_logs", sa.Column("attempt_no", sa.Integer(), nullable=True))
    op.add_column("llm_request_logs", sa.Column("request_id", sa.Uuid(), nullable=True))
    op.create_index("ix_llm_request_logs_request_id", "llm_request_logs", ["request_id"])
    op.create_unique_constraint(
        "uq_llm_request_logs_request_attempt",
        "llm_request_logs",
        ["request_id", "attempt_no"],
    )
