"""Base App／Farcaster mini app 通知 token（c049）

宿主（Base App、Farcaster 客戶端）在使用者「加入」後把 notification token 送到
manifest 的 webhookUrl；存下來早報就能多一個推播管道。fid→user_id 由前端登入後回報。

Rollback：``alembic downgrade c048``（DROP TABLE）。
"""

from alembic import op

revision = "c049"
down_revision = "c048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS miniapp_notification_tokens (
            id              BIGSERIAL PRIMARY KEY,
            fid             BIGINT NOT NULL,
            app_fid         BIGINT,
            user_id         TEXT,
            url             TEXT NOT NULL,
            token           TEXT NOT NULL,
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            disabled_reason TEXT,
            last_sent_at    TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (fid, url)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_miniapp_notif_user "
        "ON miniapp_notification_tokens (user_id) WHERE enabled"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS miniapp_notification_tokens")
