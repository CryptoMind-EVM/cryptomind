"""drop_daily_board

Revision ID: c021_drop_daily_board
Revises: c020_config_audit_log

移除「每日趨勢板」功能（core/board/ 整個模組、cron-worker 服務、前端 tab、
TG /today 指令皆已移除）。互動式 AI agent 已能即時分析任意標的，固定排程
產生的每日板子重複且死板，故整功能退場。

本 migration 對稱地撤銷 c006_add_daily_board 建立的 table + index。
"""

from alembic import op


revision = "c021_drop_daily_board"
down_revision = "c020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DROP TABLE 已會一併移除 idx_daily_board_date（index 隨表刪除）。
    op.execute("DROP TABLE IF EXISTS daily_board")


def downgrade() -> None:
    # 還原 c006 建立的 schema（僅結構，歷史 payload 資料無法復原）。
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
