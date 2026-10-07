"""journal_revisions——帳本版本史（append-only 修訂快照）

每次 create/update/delete/restore 與主變更同一個 transaction 內寫入一筆
完整欄位快照＋顯示用 diff。還原＝套用快照＋新增 restore 修訂，歷史只增
不改。背景：update_entry 就地覆蓋（舊值消失，凍結匯率被當下匯率蓋掉）；
刪除雖 soft delete（c035）但救回僅能 ops SQL。
（docs/plans/2026-08-26-journal-version-history-design.md，DANNY Approve）
"""
from alembic import op

revision = "c037"
down_revision = "c036"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS journal_revisions (
            id             BIGSERIAL PRIMARY KEY,
            entry_id       INTEGER NOT NULL REFERENCES trade_journal(id),
            user_id        VARCHAR(255) NOT NULL,
            revision_no    INTEGER NOT NULL,
            action         VARCHAR(10) NOT NULL,
            changed_fields JSONB DEFAULT NULL,
            snapshot       JSONB NOT NULL,
            source         VARCHAR(10) NOT NULL DEFAULT 'manual',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (entry_id, revision_no)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_journal_revisions_entry "
        "ON journal_revisions(user_id, entry_id, revision_no DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS journal_revisions")
