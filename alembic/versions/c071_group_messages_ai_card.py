"""群組訊息允許 message_type = 'ai_card'（c071）

2026-10-04 DANNY：AI 助理的回答「分享到聊天室」改成送出一則 AI 分析卡片（不再塞輸入框、
不受單則 500 字限制、表格／標題照原樣顯示），見 core/ai_card.py。
- group_messages.message_type 的 CHECK 放寬成 ('text', 'recalled', 'system', 'ai_card')。
  只動約束，不動任何資料。dm_messages.message_type 本來就沒有 CHECK，不用改。
Rollback：``alembic downgrade c070``——已存在的 ai_card 先改回 text（內容保留），再收緊約束。
"""

from alembic import op

revision = "c071"
down_revision = "c070"
branch_labels = None
depends_on = None

_NAME = "group_messages_message_type_check"


def upgrade() -> None:
    op.execute(f"ALTER TABLE group_messages DROP CONSTRAINT IF EXISTS {_NAME}")
    op.execute(
        f"ALTER TABLE group_messages ADD CONSTRAINT {_NAME} "
        "CHECK (message_type IN ('text', 'recalled', 'system', 'ai_card'))"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE group_messages SET message_type = 'text' WHERE message_type = 'ai_card'"
    )
    op.execute(f"ALTER TABLE group_messages DROP CONSTRAINT IF EXISTS {_NAME}")
    op.execute(
        f"ALTER TABLE group_messages ADD CONSTRAINT {_NAME} "
        "CHECK (message_type IN ('text', 'recalled', 'system'))"
    )
