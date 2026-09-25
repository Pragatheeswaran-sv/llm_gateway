"""Add account priority and a shared routing turn sequence.

Revision ID: a19c2d3e4f50
Revises: 86a3dbae08e1
"""
from alembic import op
import sqlalchemy as sa

revision = "a19c2d3e4f50"
down_revision = "86a3dbae08e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("llm_fallback_models", sa.Column("priority", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_llm_fallback_priority_positive", "llm_fallback_models", "priority IS NULL OR priority > 0")
    sa.Sequence("llm_fallback_account_turn", start=1).create(op.get_bind())


def downgrade():
    sa.Sequence("llm_fallback_account_turn").drop(op.get_bind())
    op.drop_constraint("ck_llm_fallback_priority_positive", "llm_fallback_models", type_="check")
    op.drop_column("llm_fallback_models", "priority")
