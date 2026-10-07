"""帳本報表基準幣（c039）

trade_journal 的 converted_amount / exchange_rate 都是「換算成基準幣」的
派生值，基準幣此前寫死 TWD（TradeJournalRepo 預設值，唯一建構點沒傳值）
——非台灣使用者的所有彙總都被迫以台幣呈現。改為 per-user 設定，沿用
users.language 的欄位模式（NULL＝TWD，向後相容）。
"""
from alembic import op

revision = "c039"
down_revision = "c038"


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS base_currency TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS base_currency")
