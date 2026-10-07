"""私訊回覆引用：dm_messages.reply_to_message_id（c059）

2026-09-29 DANNY：私訊要能像 LINE 一樣針對某一句回覆。存被回覆那則的 id；原訊息被刪掉時
設成 NULL（引用區塊就不顯示），被收回時原文本來就清空了（c058）。部分索引只給有回覆的列，
讓刪除原訊息時的 SET NULL 不用掃全表。Rollback：``alembic downgrade c058``（拿掉欄位，
既有回覆變一般訊息）。
"""

from alembic import op

revision = "c059"
down_revision = "c058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE dm_messages ADD COLUMN IF NOT EXISTS reply_to_message_id "
        "INTEGER REFERENCES dm_messages(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_messages_reply_to ON dm_messages(reply_to_message_id) "
        "WHERE reply_to_message_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dm_messages_reply_to")
    op.execute("ALTER TABLE dm_messages DROP COLUMN IF EXISTS reply_to_message_id")
