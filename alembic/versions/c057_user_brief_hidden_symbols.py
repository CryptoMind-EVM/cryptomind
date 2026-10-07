"""早報不列的標的：市場＋代號（c057）

2026-09-28 DANNY：使用者要能自己決定哪幾檔出現在早報——自選清單跟市場分頁共用，
刪掉就兩邊都沒了；持倉來自投資日誌，原本完全拿不掉。這張表只記「早報不要列」的那幾檔，
持倉與自選都適用，不動 user_watchlist／帳本。一列＝使用者＋市場＋代號（大寫），
帳號刪除跟著 CASCADE。Rollback：``alembic downgrade c056``（刪表；早報回到全部列出）。
"""

from alembic import op

revision = "c057"
down_revision = "c056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_brief_hidden_symbols (
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            market      TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, market, symbol)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_brief_hidden_symbols")
