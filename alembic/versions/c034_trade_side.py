"""trade_journal 加 side 欄位（buy/sell）

2026-08-22 盤點發現：表沒有 side 欄——record_entry 收了 side 參數但
INSERT 沒寫入、get_positions 的 _infer_side 讀不到→全部預設 buy→
「賣出 0.05」被當加倉（0.1+0.05=0.15 而非淨倉 0.05）。
"""
from alembic import op

revision = "c034"
down_revision = "c033"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS side "
        "VARCHAR(4) NOT NULL DEFAULT 'buy'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE trade_journal DROP COLUMN IF EXISTS side")
