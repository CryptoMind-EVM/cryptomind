"""drop_jwt_keys_table

Revision ID: c002_drop_jwt_keys_table
Revises: c001_add_jwt_keys_table
Create Date: 2026-03-29
"""

from alembic import op


revision = "c002_drop_jwt_keys_table"
down_revision = "c001_add_jwt_keys_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 冪等：jwt_keys 在正式 DB 可能根本不存在（init_db 不建它）。
    op.execute("DROP INDEX IF EXISTS idx_jwt_keys_is_primary")
    op.execute("DROP INDEX IF EXISTS idx_jwt_keys_status")
    op.execute("DROP TABLE IF EXISTS jwt_keys")


def downgrade() -> None:
    pass
