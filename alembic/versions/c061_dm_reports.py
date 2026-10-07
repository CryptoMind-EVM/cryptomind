"""私訊檢舉（c061）

2026-09-29 DANNY：收回會清空原文（c058），證據改在檢舉當下留——snapshot 存被檢舉那則＋
前 10 則的文字（後端撈，不收前端傳的）。同一人對同一則只能檢舉一次（UNIQUE）。只有管理員
看得到，不重用論壇的 content_reports（那套走社群投票，私訊不能給其他會員看）。
帳號刪除跟著 CASCADE；訊息被刪掉時 message_id 設 NULL，快照還在。
Rollback：``alembic downgrade c060``（刪表，檢舉紀錄全部消失）。
"""

from alembic import op

revision = "c061"
down_revision = "c060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dm_reports (
            id                SERIAL PRIMARY KEY,
            reporter_user_id  TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            reported_user_id  TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            conversation_id   INTEGER NOT NULL REFERENCES dm_conversations(id) ON DELETE CASCADE,
            message_id        INTEGER REFERENCES dm_messages(id) ON DELETE SET NULL,
            reason            TEXT NOT NULL CHECK (reason IN ('scam', 'harassment', 'spam', 'other')),
            note              TEXT,
            snapshot          JSONB NOT NULL,
            status            TEXT NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'resolved', 'dismissed')),
            admin_note        TEXT,
            resolved_by       TEXT,
            resolved_at       TIMESTAMPTZ,
            created_at        TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_dm_report UNIQUE (reporter_user_id, message_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_reports_status ON dm_reports(status, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dm_reports")
