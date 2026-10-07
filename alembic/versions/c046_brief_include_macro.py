"""早報偏好加 include_macro（c046）

設計：docs/plans/2026-09-12-wallet-ledger-calendar-integration.md §1

總經事件（FOMC／CPI／非農）進行事曆是全平台事件，不看持倉；讓不想看的人關掉。
預設 TRUE（與「綁 Telegram 就開早報」同一口徑：先給、再讓人關）。

Rollback：``alembic downgrade c045``（DROP COLUMN）。
"""

from alembic import op

revision = "c046"
down_revision = "c045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_brief_prefs ADD COLUMN IF NOT EXISTS "
        "include_macro BOOLEAN NOT NULL DEFAULT TRUE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE user_brief_prefs DROP COLUMN IF EXISTS include_macro")
