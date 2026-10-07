"""每日早報偏好＋事件日曆（c045，留存核心第 1～3 項）

設計：docs/plans/2026-09-12-daily-brief-retention-design.md §6

- ``user_brief_prefs``：一人一列；沒列＝用預設（綁 Telegram 的人預設開、本地
  08:00、含昨日花費）。``last_sent_on`` 擋同一天重送（cron 每小時跑）。
- ``user_calendar_events``：系統事件（財報／月營收／解鎖／總經）與使用者自訂
  事件同一張表；``dedupe_key`` 讓系統事件每天重抓不重複。

設計文件原本還要在 price_alerts 加 ``triggered_at``；實作時改讀 notifications
（警報觸發本來就會寫一筆 in-app 通知，且一次性警報觸發即刪除、加欄也留不住），
所以這支 migration 不動 price_alerts。

Rollback：``alembic downgrade c044``（兩張表直接 DROP，沒有回填資料）。
"""

from alembic import op

revision = "c045"
down_revision = "c044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_brief_prefs (
            user_id        TEXT PRIMARY KEY,
            enabled        BOOLEAN NOT NULL DEFAULT TRUE,
            send_hour      SMALLINT NOT NULL DEFAULT 8,
            timezone       TEXT NOT NULL DEFAULT 'Asia/Taipei',
            channels       TEXT[] NOT NULL DEFAULT '{telegram,inapp}',
            include_spend  BOOLEAN NOT NULL DEFAULT TRUE,
            last_sent_on   DATE,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_calendar_events (
            id                 BIGSERIAL PRIMARY KEY,
            user_id            TEXT NOT NULL,
            event_date         DATE NOT NULL,
            title              TEXT NOT NULL,
            kind               TEXT NOT NULL DEFAULT 'custom',
            symbol             TEXT,
            market             TEXT,
            source             TEXT NOT NULL DEFAULT 'user',
            remind_days_before SMALLINT NOT NULL DEFAULT 1,
            note               TEXT NOT NULL DEFAULT '',
            dedupe_key         TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, dedupe_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_calendar_events_user_date "
        "ON user_calendar_events (user_id, event_date)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_calendar_events")
    op.execute("DROP TABLE IF EXISTS user_brief_prefs")
