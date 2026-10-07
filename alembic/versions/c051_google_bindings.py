"""Google 帳號綁定（c051）

設計：docs/plans/2026-09-13-google-play-twa-design.md §4。網頁版與 Google Play 版的 Google 登入；
一個 Google 帳號 ↔ 一個平台帳號（比照 telegram_bindings）。

Rollback：``alembic downgrade c050``（DROP TABLE）。
"""

from alembic import op

revision = "c051"
down_revision = "c050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS google_bindings (
            google_sub    TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL UNIQUE,
            email         TEXT,
            display_name  TEXT,
            linked_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_used_at  TIMESTAMPTZ,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS google_bindings")
