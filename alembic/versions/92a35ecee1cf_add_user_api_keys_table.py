"""add_user_api_keys_table

Revision ID: 92a35ecee1cf
Revises: 4107b7e75608
Create Date: 2026-03-10 09:48:15.735458

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "92a35ecee1cf"
down_revision: Union[str, Sequence[str], None] = "4107b7e75608"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add user_api_keys table (idempotent).

    正式 DB 的表由 init_db() 在 runtime 建立，而 alembic_version 仍停在
    baseline，故 `alembic upgrade head` 會走到這裡並嘗試重建已存在的表，
    造成 DuplicateTable。改用 CREATE TABLE/INDEX IF NOT EXISTS，讓本
    migration 可安全（且可重複）跑在 init_db 已建好的 DB 上 —— 與 b003
    的冪等補表 philosophy 一致。
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_api_keys (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            encrypted_key TEXT NOT NULL,
            model_selection TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP,
            UNIQUE (user_id, provider),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_api_keys_user "
        "ON user_api_keys(user_id)"
    )


def downgrade() -> None:
    """Downgrade schema: remove user_api_keys table."""
    op.drop_index("idx_user_api_keys_user", table_name="user_api_keys")
    op.drop_table("user_api_keys")
