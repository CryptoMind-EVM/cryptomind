"""後台覆寫的開關與額度（c055）

2026-09-27 DANNY：開關與額度要能在後台切（core/setting_overrides.py）。一個 key 一列；
有列＝後台覆寫，刪列＝回到環境變數。改動歷史寫在既有的 config_audit_log。

純新增一張表，不動既有欄位。Rollback：``alembic downgrade c054``（DROP TABLE；
所有覆寫失效，回到環境變數的值）。
"""

from alembic import op

revision = "c055"
down_revision = "c054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_setting_overrides (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_by  TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS admin_setting_overrides")
