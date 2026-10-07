"""私訊表情回應（c060）

2026-09-29 DANNY：私訊要有 LINE 式表情回應，8 個自繪 SVG（讚、愛心、哈哈、哇、嗚、衝、
鑽石手、收到）。DB 只存 key，CHECK 擋掉清單外的值（清單在 core/dm_reactions.py）。
每人每則一個（UNIQUE），訊息刪掉或帳號刪掉跟著 CASCADE。UNIQUE 的索引以 message_id 開頭，
讀取時依訊息撈不用另建索引。Rollback：``alembic downgrade c059``（刪表，表情全部消失）。
"""

from alembic import op

revision = "c060"
down_revision = "c059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dm_message_reactions (
            id          SERIAL PRIMARY KEY,
            message_id  INTEGER NOT NULL REFERENCES dm_messages(id) ON DELETE CASCADE,
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            reaction    TEXT NOT NULL CHECK (reaction IN
                ('like', 'love', 'haha', 'wow', 'sad', 'rocket', 'diamond', 'ok')),
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_dm_message_reaction UNIQUE (message_id, user_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dm_message_reactions")
