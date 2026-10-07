"""drop_pi_columns

Revision ID: c003_drop_pi_columns
Revises: c002_drop_jwt_keys_table
Create Date: 2026-06-15

Remove the legacy Pi Network identity columns from `users`.
Since `user_id` already holds the wallet address for every user (Pi users
had user_id == pi_uid, and TON users store the wallet address as user_id),
`pi_uid` was a redundant duplicate of the primary key and `pi_username`
was display-only. Dropping them loses no identity information.

Revision ID: c003_drop_pi_columns
"""

import sqlalchemy as sa
from alembic import op

revision = "c003_drop_pi_columns"
down_revision = "c002_drop_jwt_keys_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Index was created by scripts/add_indexes.py on legacy databases.
    op.execute("DROP INDEX IF EXISTS idx_users_pi_uid")
    # 冪等：現行 init_db 建的 users 表已不含 pi 欄位，故用 IF EXISTS，
    # 避免在沒有這些欄位的正式 DB 上 DROP COLUMN 失敗。
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS pi_uid")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS pi_username")


def downgrade() -> None:
    # Restore the columns (empty — historical Pi data is not recoverable,
    # but the schema shape is re-created so a rolled-back deploy boots).
    op.add_column("users", sa.Column("pi_uid", sa.Text(), unique=True))
    op.add_column("users", sa.Column("pi_username", sa.Text()))
