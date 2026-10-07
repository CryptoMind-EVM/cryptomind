"""config_audit_log table

Revision ID: c020
Revises: c019
Create Date: 2026-08-12

修復既有 bug：config_audit_log 表從未被建立，但 4 個地方（system_config.py、
config_repo.py、admin/config.py、admin/users.py）都寫這張表 → 所有 admin
設定變更/角色變更的審計 INSERT 都拋 UndefinedTable。PR #436 曾用容錯
（catch + log warning）讓主操作不掛，但審計資料仍是靜默丟失。

這張表是 config_audit_log（config_key / old_value / new_value / changed_by /
changed_at），與 users.py 的 INSERT（沒帶 changed_at，靠 DB default）相容。
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "c020"
down_revision = "c019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS config_audit_log (
            id          SERIAL PRIMARY KEY,
            config_key  TEXT NOT NULL,
            old_value   TEXT,
            new_value   TEXT,
            changed_by  TEXT,
            changed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_config_audit_log_key "
        "ON config_audit_log (config_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_config_audit_log_changed_at "
        "ON config_audit_log (changed_at DESC)"
    )


def downgrade() -> None:
    """移除表（down migration 完整逆轉）。"""
    op.execute("DROP TABLE IF EXISTS config_audit_log")
