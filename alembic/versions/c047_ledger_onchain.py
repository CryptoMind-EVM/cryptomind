"""帳本鏈上同步（c047）

設計：docs/plans/2026-09-12-wallet-ledger-calendar-integration.md §2

- ``trade_journal`` 加 ``chain``／``tx_hash``：鏈上同步進來的條目帶來源，唯一部分索引
  ``(user_id, chain, tx_hash, symbol, side) WHERE tx_hash IS NOT NULL`` 讓每天重跑冪等
  （同一筆轉帳不會重複記；使用者刪掉的也不會再回來——查重含 deleted）。
- ``user_onchain_sync``：一人一列的同步狀態（開關、上次同步、上次結果）。

Rollback：``alembic downgrade c046``（DROP 索引與表；欄位留著不傷資料）。
"""

from alembic import op

revision = "c047"
down_revision = "c046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS chain VARCHAR(20)")
    op.execute("ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS tx_hash VARCHAR(90)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_journal_onchain "
        "ON trade_journal (user_id, chain, tx_hash, symbol, side) "
        "WHERE tx_hash IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_onchain_sync (
            user_id        TEXT PRIMARY KEY,
            enabled        BOOLEAN NOT NULL DEFAULT TRUE,
            last_synced_at TIMESTAMPTZ,
            last_result    JSONB,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_onchain_sync")
    op.execute("DROP INDEX IF EXISTS uq_trade_journal_onchain")
