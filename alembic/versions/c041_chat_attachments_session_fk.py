"""chat_attachments → sessions FK ON DELETE CASCADE（c041，vision Phase 2）

設計：docs/plans/2026-08-30-vision-image-storage-design.md
刪除對話（core/database/chat.py:delete_session 的 DELETE FROM sessions）時，
由 DB 層連動刪除該對話所有附件——應用層零代碼，且涵蓋任何未來刪除
sessions 列的路徑（如帳號刪除）。

註：表本體由 schema.py 的 init_db 保底建（啟動即建）；c040 建表遷移
已移除（與 schema.py 重複，生產啟動 DuplicateTable）。本遷移只建 FK。
"""

from alembic import op

revision = "c041"
down_revision = "c039"


def upgrade() -> None:
    op.create_foreign_key(
        "fk_chat_attachments_session",
        "chat_attachments",
        "sessions",
        ["session_id"],
        ["session_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_chat_attachments_session", "chat_attachments", type_="foreignkey"
    )
