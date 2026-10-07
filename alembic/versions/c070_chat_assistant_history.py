"""聊天室 AI 助理的問答紀錄（c070）

2026-10-04 DANNY：分析完關掉抽屜（誤觸、切換聊天室、換裝置）答案就消失，體驗差，改成存伺服器、
跨裝置接續（推翻 2026-10-01 設計的「不存資料庫」）。
- chat_assistant_history：每人每個聊天室留最近 10 則、7 天；只有提問的人讀得到。
  target_id 依 kind 指 dm_conversations.id 或 group_chats.id（多型，不設 FK，同 chat_pins）。
  source_ids＝回答讀過的訊息 id（INTEGER[] ＋ GIN）：其中任一則被收回就整列刪掉，
  別人收回的內容不會因為「AI 整理過」而留在這裡。
  users 刪除時 CASCADE 一起刪。
Rollback：``alembic downgrade c069``（刪表；已存的問答全部消失，不影響其他資料）。
"""

from alembic import op

revision = "c070"
down_revision = "c069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_assistant_history (
            id          BIGSERIAL PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            kind        TEXT NOT NULL CHECK (kind IN ('dm', 'group')),
            target_id   INTEGER NOT NULL,
            question    TEXT NOT NULL,
            answer      TEXT NOT NULL,
            source_ids  INTEGER[] NOT NULL DEFAULT '{}',
            meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_assistant_history_chat "
        "ON chat_assistant_history(user_id, kind, target_id, id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_assistant_history_sources "
        "ON chat_assistant_history USING GIN (source_ids)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_assistant_history")
