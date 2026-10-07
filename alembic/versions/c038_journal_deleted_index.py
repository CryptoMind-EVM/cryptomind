"""回收筒部分索引（c038）

list_deleted（回收筒）查詢 WHERE user_id + ORDER BY deleted_at DESC——
既有 trade_journal 索引（user/traded_at、symbol、category、type_date、
note trgm）都不涵蓋 deleted_at 述詞。補部分索引對齊 schema.py（冪等，
IF NOT EXISTS；2026-08-26 review I2）。
"""
from alembic import op

revision = "c038"
down_revision = "c037"


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_deleted "
        "ON trade_journal(user_id, deleted_at DESC) WHERE deleted_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_trade_journal_deleted")
