"""trade_journal — 投資帳本表（docs/plans/2026-08-21-investment-journal-design.md）

Revision ID: c032
Revises: c031
Create Date: 2026-08-21

DANNY Approve（「確定 Approve」2026-08-21）。

支援：
- 跨市場（crypto / tw_stock / us_stock / hk_stock / jp_stock / forex / commodity）
- 現貨（spot）/ 合約（futures）/ 融資（margin）
- 做多（long）/ 做空（short）——現貨恆 long
- 槓桿（現貨=1、合約可 >1）

數量單位依市場（幣量/張/股/手/口），統一 NUMERIC(20,8)——顯示層格式化。
不追蹤保證金/強平——帳本不是交易所。
"""

from alembic import op

revision = "c032"
down_revision = "c031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_journal (
            id              SERIAL PRIMARY KEY,
            user_id         VARCHAR(255) NOT NULL,

            symbol          VARCHAR(30) NOT NULL,
            market          VARCHAR(15) NOT NULL,
            instrument_type VARCHAR(10) NOT NULL DEFAULT 'spot',
            direction       VARCHAR(5) NOT NULL DEFAULT 'long',
            leverage        NUMERIC(6,2) NOT NULL DEFAULT 1,

            quantity        NUMERIC(20,8) NOT NULL,
            price           NUMERIC(20,8) NOT NULL,
            currency        VARCHAR(5) NOT NULL DEFAULT 'USD',
            fee             NUMERIC(20,8) DEFAULT 0,
            fee_currency    VARCHAR(5) DEFAULT '',

            traded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source          VARCHAR(10) DEFAULT 'chat',
            note            TEXT DEFAULT '',

            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_user "
        "ON trade_journal(user_id, traded_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_symbol "
        "ON trade_journal(user_id, symbol)"
    )


def downgrade() -> None:
    # 帳本表留著無害（使用者的交易記錄）
    pass
