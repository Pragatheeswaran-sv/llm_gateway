"""Add per-attempt LLM request logs.

Revision ID: b20d3e4f5a61
Revises: a19c2d3e4f50
"""
from alembic import op
import sqlalchemy as sa

revision = "b20d3e4f5a61"
down_revision = "a19c2d3e4f50"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_request_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("llm_fallback_model_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("daily_token_limit", sa.BigInteger(), nullable=True),
        sa.Column("daily_request_limit", sa.Integer(), nullable=True),
        sa.Column("rpm_limit", sa.Integer(), nullable=True),
        sa.Column("tpm_limit", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["llm_fallback_model_id"], ["llm_fallback_models.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "attempt_no", name="uq_llm_request_logs_request_attempt"),
    )
    op.create_index("ix_llm_request_logs_request_id", "llm_request_logs", ["request_id"])
    op.create_index("ix_llm_request_logs_llm_fallback_model_id", "llm_request_logs", ["llm_fallback_model_id"])


def downgrade():
    op.drop_index("ix_llm_request_logs_llm_fallback_model_id", table_name="llm_request_logs")
    op.drop_index("ix_llm_request_logs_request_id", table_name="llm_request_logs")
    op.drop_table("llm_request_logs")
