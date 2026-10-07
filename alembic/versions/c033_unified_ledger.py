"""unified_ledger — 統一帳本擴充（投資＋支出＋收入×多幣種×自動匯率）

Revision ID: c033
Revises: c032
Create Date: 2026-08-21

DANNY Approve：「統一帳本（投資＋支出）× 自動匯率 × 匯率凍結 × 可覆蓋」
＋ 類別系統（餐飲/交通/生活/...）＋ 查詢功能 ＋ 備註欄位

新增欄位：
- entry_type: trade / expense / income
- category: food/transport/housing/shopping/entertainment/medical/education/investment/income/other
- base_currency: 使用者本位幣（預設 TWD）
- converted_amount: 記錄時換算成 base_currency 的金額（匯率凍結）
- exchange_rate: 記錄時匯率（1 unit = ? base_currency）
- rate_source: auto（API 抓）/ manual（使用者覆蓋）
"""

from alembic import op

revision = "c033"
down_revision = "c032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 帳本類型（trade=投資 / expense=支出 / income=收入）
    op.execute(
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
        "entry_type VARCHAR(10) NOT NULL DEFAULT 'trade'"
    )
    # 類別
    op.execute(
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
        "category VARCHAR(20) NOT NULL DEFAULT 'other'"
    )
    # 本位幣＋匯率凍結
    op.execute(
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
        "base_currency VARCHAR(5) NOT NULL DEFAULT 'TWD'"
    )
    op.execute(
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
        "converted_amount NUMERIC(20,8) DEFAULT NULL"
    )
    op.execute(
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
        "exchange_rate NUMERIC(20,8) DEFAULT NULL"
    )
    op.execute(
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
        "rate_source VARCHAR(10) NOT NULL DEFAULT 'auto'"
    )
    # 類別索引（查詢加速）
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_category "
        "ON trade_journal(user_id, entry_type, category)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_type_date "
        "ON trade_journal(user_id, entry_type, traded_at DESC)"
    )


def downgrade() -> None:
    # 欄位留著無害
    pass
