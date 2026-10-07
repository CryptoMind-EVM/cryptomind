"""對話自訂順序＋群主解散群組（c068）

2026-10-01 DANNY：
- chat_order：社群列表（私訊＋群組混排）的「自訂順序」模式。置頂（chat_pins）永遠在最上面，
  其他照使用者拖的順序；新訊息不會把對話往上推，還沒有位置的（新對話）排在非置頂區最上面。
  target_id 依 kind 指 dm_conversations.id 或 group_chats.id（多型，不設 FK，同 chat_pins）；
  退群／看不到的對話留下的列在每次排序時清掉。排序模式本身存在前端，不進 DB。
- group_chats.dissolved_at：群主解散群組。成員全部移出、待處理邀請取消，訊息留著當檢舉證據
  （所以不能直接刪群：group_messages／group_reports 會被 CASCADE 一起刪掉）。
Rollback：``alembic downgrade c067``（刪 chat_order、拿掉 dissolved_at；自訂順序全部消失，
已解散的群組成員已經移出，不會因此復活）。
"""

from alembic import op

revision = "c068"
down_revision = "c067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_order (
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            kind        TEXT NOT NULL CHECK (kind IN ('dm', 'group')),
            target_id   INTEGER NOT NULL,
            position    INTEGER NOT NULL,
            PRIMARY KEY (user_id, kind, target_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_order_user_position ON chat_order(user_id, position)"
    )
    op.execute(
        "ALTER TABLE group_chats ADD COLUMN IF NOT EXISTS dissolved_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_order")
    op.execute("ALTER TABLE group_chats DROP COLUMN IF EXISTS dissolved_at")
