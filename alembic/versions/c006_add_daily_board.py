"""add_daily_board

Revision ID: c006
Revises: c005

每日趨勢板（公共區）。一天生成一次、所有人看同一份，由獨立的批次 worker
（scripts/cron_daily_board.py）寫入、web 服務只讀。

- 每個 (board_date, scope) 唯一，重跑覆蓋當天那份（UPSERT）。
- payload 存「已排版好的 web 結構」，讓讀取 API 直接回傳、零加工。
- scope 預留未來做分市場板（'global' / 'crypto' / 'us' ...）。
"""

from alembic import op


revision = "c006"
down_revision = "c005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_board (
            id           SERIAL PRIMARY KEY,
            board_date   DATE NOT NULL,
            scope        TEXT NOT NULL DEFAULT 'global',
            payload      JSONB NOT NULL,
            model_used   TEXT,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (board_date, scope)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_board_date "
        "ON daily_board (board_date DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_board")
