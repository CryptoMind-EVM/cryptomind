"""對話置頂＋拖曳排序（c067）

2026-10-01：社群列表（私訊＋群組混排）與 AI 對話側欄都要能置頂、置頂的能拖曳排順序
（Telegram 模式：置頂的照使用者排的順序在最上面，其他照最後動態）。
- chat_pins：私訊與群組共用一張，target_id 依 kind 指 dm_conversations.id 或 group_chats.id
  （多型，不設 FK）；退群／被踢留下的失效置頂在每次置頂操作時清掉。上限在程式裡（私訊＋群組合計）。
- sessions.pin_order：AI 對話本來就有 is_pinned（星號），補一欄記拖曳順序；
  取消置頂設 NULL，舊的置頂列是 NULL（排在有編號的後面，照 updated_at）。
Rollback：``alembic downgrade c066``（刪 chat_pins、拿掉 sessions.pin_order，置頂順序全部消失；
AI 對話的 is_pinned 不受影響）。
"""

from alembic import op

revision = "c067"
down_revision = "c066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_pins (
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            kind        TEXT NOT NULL CHECK (kind IN ('dm', 'group')),
            target_id   INTEGER NOT NULL,
            position    INTEGER NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (user_id, kind, target_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_pins_user_position ON chat_pins(user_id, position)"
    )
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS pin_order INTEGER")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_pins")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS pin_order")
