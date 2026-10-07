"""使用者自選清單：市場＋代號（c056）

2026-09-27 DANNY：使用者要能自己挑早報與價格警報要追蹤的標的。舊表 ``watchlist``
只存代號、不分市場（早報一律當加密貨幣抓價，SOL／OP 這種代號也分不清是幣還是美股），
而且前端從來沒有入口（正式機 0 筆）。改成 ``user_watchlist``：一列＝使用者＋市場＋代號，
帳號刪除跟著 CASCADE。

舊表的列搬過來時市場記成 crypto——跟舊版早報的解讀一致；搬完刪掉舊表，免得又多一張
「跟現在不攏」的表。Rollback：``alembic downgrade c055``（重建舊表、搬回代號、刪新表）。
"""

import sqlalchemy as sa
from alembic import op

revision = "c056"
down_revision = "c055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_watchlist (
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            market      TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, market, symbol)
        )
        """
    )
    has_old = op.get_bind().execute(sa.text("SELECT to_regclass('public.watchlist')")).scalar()
    if has_old:
        op.execute(
            """
            INSERT INTO user_watchlist (user_id, market, symbol)
            SELECT w.user_id, 'crypto', UPPER(w.symbol)
            FROM watchlist w JOIN users u ON u.user_id = w.user_id
            ON CONFLICT DO NOTHING
            """
        )
        op.execute("DROP TABLE watchlist")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id TEXT,
            symbol TEXT,
            PRIMARY KEY (user_id, symbol)
        )
        """
    )
    op.execute(
        "INSERT INTO watchlist (user_id, symbol) "
        "SELECT user_id, symbol FROM user_watchlist ON CONFLICT DO NOTHING"
    )
    op.execute("DROP TABLE IF EXISTS user_watchlist")
