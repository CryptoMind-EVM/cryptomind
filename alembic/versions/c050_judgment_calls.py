"""判斷評分 v2：明確喊單（c050）

設計：docs/plans/2026-09-13-judgment-scoring-design.md §7。
使用者在聊天或帳本明講「看多／看空 X，期限 N 天（可選目標價）」就記一筆；
到期由既有 judgment_scores（kind='call'，call_id 指回這裡）評分。

Rollback：``alembic downgrade c049``（DROP TABLE）。
"""

from alembic import op

revision = "c050"
down_revision = "c049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS judgment_calls (
            id            BIGSERIAL PRIMARY KEY,
            user_id       TEXT NOT NULL,
            symbol        TEXT NOT NULL,
            market        TEXT NOT NULL,
            side          TEXT NOT NULL,
            horizon_days  SMALLINT NOT NULL,
            target_price  NUMERIC(20,8),
            entry_price   NUMERIC(20,8) NOT NULL,
            entry_date    DATE NOT NULL,
            note          TEXT NOT NULL DEFAULT '',
            source        TEXT NOT NULL DEFAULT 'chat',
            status        TEXT NOT NULL DEFAULT 'open',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            cancelled_at  TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgment_calls_user "
        "ON judgment_calls (user_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS judgment_calls")
