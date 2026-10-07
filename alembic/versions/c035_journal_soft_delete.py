"""trade_journal soft delete——deleted_at 欄

2026-08-24 盤點：delete_trade 是硬刪除（DELETE FROM），違反 AGENTS.md
soft delete 慣例；疊加前端刪除確認失效（tab-journal.js 條件反轉），
誤刪即永久消失。改 soft delete：刪除=標記 deleted_at，所有讀取過濾。
（docs/plans/2026-08-24-journal-delete-safety-design.md，DANNY Approve）
"""
from alembic import op

revision = "c035"
down_revision = "c034"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS deleted_at "
        "TIMESTAMPTZ DEFAULT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE trade_journal DROP COLUMN IF EXISTS deleted_at")
