"""drop_swap_tables — swap 功能整體移除（DANNY 2026-08-21 核准）

Revision ID: c030
Revises: c029
Create Date: 2026-08-21

swap（換幣）功能於 2026-08-10 整體關閉後，2026-08-21 DANNY 核准整個功能
清除（docs/plans/2026-08-05-swap-tech-debt-design.md 標記 Superseded —
7 項技術債隨移除歸零）。本 migration 清掉 swap 專屬的兩張表：

- ⚠️ ``used_payments`` **保留不刪**——歷史註解寫「premium + swap 共用」，
  實查 premium.py:_record_used_payment 仍在活用（會員付款 replay 防護，
  "payment already used" 檢查）。schema.py 建表已保留（註解改為 premium 專屬）。
- DROP TABLE ``swap_executions``——換幣明細（TD-08 落地），僅 swap.py 寫入。
- DROP TABLE ``user_swap_limits``——per-user 換幣上限（2026-08-09 設計），
  僅 swap_limits_repo（已刪）讀寫。

production 從未啟用 swap（OMNISTON_ENABLED 恆 false），表內至多測試資料。
Idempotent（IF EXISTS）；downgrade 不重建（功能已移除，無意義）。
"""

from alembic import op

revision = "c030"
down_revision = "c029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS swap_executions")
    op.execute("DROP TABLE IF EXISTS user_swap_limits")


def downgrade() -> None:
    # 功能已整體移除——downgrade 不重建（資料本來就不存在/不可復原）
    pass
