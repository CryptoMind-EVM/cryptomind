"""服務條款／隱私政策同意紀錄（c054）

2026-09-27 DANNY：條款改版後要使用者重新確認同意（條款第 15 條、隱私政策第 10 條的承諾）。
一版一列、保留歷史（誰在何時同意了哪一版），版本號見 core/legal.py 的 LEGAL_VERSION。
帳號刪除跟著 CASCADE。

純新增一張表，不動既有欄位。Rollback：``alembic downgrade c053``（DROP TABLE）。
"""

from alembic import op

revision = "c054"
down_revision = "c053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_legal_acceptances (
            user_id      TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            version      TEXT NOT NULL,
            accepted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, version)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_legal_acceptances")
