"""Email 早報訂閱（c053）

設計：docs/plans/2026-09-27-pr8-email-brief-impl.md（上市準備 §5 PR-8）。一人一列：
填 email → 寄確認信（confirm_token_hash＋confirm_sent_at，48 小時）→ 點連結 verified_at；
信末一鍵退訂寫 unsubscribed_at。token 只存 sha256，明文不落地。
使用者在設定頁移除 email＝刪列（個資不留）；帳號刪除跟著 CASCADE。
email 刻意不設 UNIQUE：同一人的多個帳號可以各自訂到同一個信箱（每個都要各自點確認）。

純新增一張表，不動既有欄位。Rollback：``alembic downgrade c052``（DROP TABLE）。
"""

from alembic import op

revision = "c053"
down_revision = "c052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_email_subscriptions (
            user_id                 TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            email                   TEXT NOT NULL,
            confirm_token_hash      TEXT,
            confirm_sent_at         TIMESTAMPTZ,
            verified_at             TIMESTAMPTZ,
            unsubscribe_token_hash  TEXT,
            unsubscribed_at         TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_sub_confirm_hash "
        "ON user_email_subscriptions (confirm_token_hash) "
        "WHERE confirm_token_hash IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_sub_unsub_hash "
        "ON user_email_subscriptions (unsubscribe_token_hash) "
        "WHERE unsubscribe_token_hash IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_sub_email "
        "ON user_email_subscriptions (email)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_email_subscriptions")
