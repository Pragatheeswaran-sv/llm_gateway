"""single_row_request_logs

Revision ID: 662fe7fe551c
Revises: d42e6f8a1c03
Create Date: 2026-09-30 11:50:33.336925

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '662fe7fe551c'
down_revision: Union[str, Sequence[str], None] = 'd42e6f8a1c03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    """Upgrade schema to single-row-per-request format."""
    op.execute(sa.text("ALTER TABLE llm_request_logs ADD COLUMN IF NOT EXISTS is_fallback_mode BOOLEAN NOT NULL DEFAULT false"))
    op.execute(sa.text("ALTER TABLE llm_request_logs ADD COLUMN IF NOT EXISTS total_attempts INTEGER NOT NULL DEFAULT 1"))
    op.execute(sa.text("ALTER TABLE llm_request_logs ADD COLUMN IF NOT EXISTS failed_attempts JSONB"))

    op.execute(sa.text("ALTER TABLE llm_request_logs ALTER COLUMN provider DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE llm_request_logs ALTER COLUMN model_name DROP NOT NULL"))

    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS requested_provider"))
    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS requested_model"))
    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS error_code"))
    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS estimated_tokens"))
    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS used_tokens"))
    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS used_requests"))
    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS minute_requests"))
    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS minute_tokens"))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(sa.text("ALTER TABLE llm_request_logs ADD COLUMN IF NOT EXISTS minute_tokens BIGINT"))
    op.execute(sa.text("ALTER TABLE llm_request_logs ADD COLUMN IF NOT EXISTS minute_requests INTEGER"))
    op.execute(sa.text("ALTER TABLE llm_request_logs ADD COLUMN IF NOT EXISTS used_requests INTEGER"))
    op.execute(sa.text("ALTER TABLE llm_request_logs ADD COLUMN IF NOT EXISTS used_tokens BIGINT"))
    op.execute(sa.text("ALTER TABLE llm_request_logs ADD COLUMN IF NOT EXISTS estimated_tokens INTEGER"))
    op.execute(sa.text("ALTER TABLE llm_request_logs ADD COLUMN IF NOT EXISTS error_code VARCHAR(40)"))

    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS failed_attempts"))
    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS total_attempts"))
    op.execute(sa.text("ALTER TABLE llm_request_logs DROP COLUMN IF EXISTS is_fallback_mode"))
