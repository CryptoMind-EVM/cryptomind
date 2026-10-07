"""Email／Google 登入（Reown 內嵌錢包）的帳號記錄：embedded_wallet_users（c064）

2026-09-30 DANNY：想在自家後台看到多少人用 Email／Google 登入、是哪些帳號——Reown 免費方案
每月 500 人、超過直接停用；將來換內嵌錢包服務，這些人的地址會變（帳號＝地址）。
audit_logs 90 天就清，所以另開一張：一個帳號一列，記方式、首次／最近登入時間與次數。
provider 是前端回報的（AppKit embeddedWalletInfo.authProvider），只拿來統計，不做權限判斷；
不存 Email 本身。

Rollback：``alembic downgrade c063``（DROP TABLE；登入不受影響，只是不再記錄）。
"""

from alembic import op

revision = "c064"
down_revision = "c063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS embedded_wallet_users (
            user_id       TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            address       TEXT NOT NULL,
            provider      TEXT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            login_count   INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_embedded_wallet_users_last_seen "
        "ON embedded_wallet_users (last_seen_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS embedded_wallet_users")
