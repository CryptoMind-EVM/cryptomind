"""已收回私訊的原文清掉（c058）

2026-09-29：收回原本只改 message_type，原文留在 DB，對話列表預覽、搜尋、讀取 API、
通知都還漏得出來。之後收回會直接清空（messages_repo.recall_message）；這支把既有的補清。
通知：預覽顯示那一則的舊通知（body 結尾＝同一段 50 字預覽）改成「訊息已收回」。
不可逆——downgrade 什麼都不做（DANNY 2026-09-29 同意清掉）。
"""

from alembic import op

revision = "c058"
down_revision = "c057"
branch_labels = None
depends_on = None

# 預覽算法同 notifications_repo._message_preview：前 50 字，超過加 ...
_PREVIEW = "left(m.content, 50) || CASE WHEN char_length(m.content) > 50 THEN '...' ELSE '' END"

PURGE_SQL = (
    # 通知要在清 content 之前比對。比整段 body（＝「名字: 預覽」），不能只比結尾：
    # 收回 "test" 時會把 "prefix: test" 的通知也改掉，而這支不可逆
    f"""
    UPDATE notifications n
    SET body = COALESCE(n.data->>'from_username', n.data->>'from_user_id', '') || ': 訊息已收回',
        data = COALESCE(n.data, '{{}}'::jsonb) || '{{"recalled": true}}'::jsonb
    FROM dm_messages m
    WHERE m.message_type = 'recalled'
      AND m.content <> ''
      AND n.user_id = m.to_user_id
      AND n.type = 'message'
      AND n.data->>'conversation_id' = m.conversation_id::text
      AND n.body = COALESCE(n.data->>'from_username', '') || ': ' || {_PREVIEW}
    """,
    "UPDATE dm_messages SET content = '' WHERE message_type = 'recalled' AND content <> ''",
)


def upgrade() -> None:
    for stmt in PURGE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    pass
