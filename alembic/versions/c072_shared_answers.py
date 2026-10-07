"""AI 回答快照分享（c072）

2026-10-05 任務 D（docs/plans/2026-10-04-growth-handoff.md）：使用者在對話裡選一輪問答按「分享」，
產生不可猜的連結，別人免登入唯讀看那一輪（含連結預覽），底部有「自己問一題」。
- shared_answers：只存使用者選的那一輪（question／answer 已遮蔽錢包地址等），不是整個對話，
  不含其他使用者資訊與工具原始輸出。
- token 本身不存，只存 SHA-256（token_hash UNIQUE）：資料庫外洩也拿不到可用連結。
- expires_at 到期（預設 30 天，程式端決定）、revoked_at 撤銷；users 刪除時 CASCADE 一起刪。
- 只新增表與索引，沒有改任何既有表；整個功能由旗標 CONVERSATION_SHARE_ENABLED 控制（預設關）。
Rollback：``alembic downgrade c071``（刪表；已分享的連結全部失效，不影響其他資料）。
"""

from alembic import op

revision = "c072"
down_revision = "c071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_answers (
            id          BIGSERIAL PRIMARY KEY,
            token_hash  TEXT NOT NULL UNIQUE,
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            question    TEXT NOT NULL,
            answer      TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ NOT NULL,
            revoked_at  TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_answers_user "
        "ON shared_answers(user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_answers_expires "
        "ON shared_answers(expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS shared_answers")
