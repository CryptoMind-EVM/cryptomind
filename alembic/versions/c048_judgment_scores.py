"""判斷評分（c048）

設計：docs/plans/2026-09-13-judgment-scoring-design.md §5

帳本每筆交易＝一個帶時間戳的判斷；到期用歷史收盤價對答案、扣同期基準指數，
凍結成一列。帳本條目之後刪掉分數仍在（堵「輸的單不算」）。

Rollback：``alembic downgrade c047``（DROP TABLE）。
"""

from alembic import op

revision = "c048"
down_revision = "c047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS judgment_scores (
            id             BIGSERIAL PRIMARY KEY,
            user_id        TEXT NOT NULL,
            entry_id       BIGINT NOT NULL DEFAULT 0,
            call_id        BIGINT NOT NULL DEFAULT 0,
            kind           TEXT NOT NULL DEFAULT 'trade',
            symbol         TEXT NOT NULL,
            market         TEXT NOT NULL,
            side           TEXT NOT NULL,
            horizon_days   SMALLINT NOT NULL,
            entry_price    NUMERIC(20,8) NOT NULL,
            entry_date     DATE NOT NULL,
            exit_price     NUMERIC(20,8),
            exit_date      DATE NOT NULL,
            raw_pct        NUMERIC(10,4),
            bench_symbol   TEXT,
            bench_pct      NUMERIC(10,4),
            excess_pct     NUMERIC(10,4),
            hit            BOOLEAN,
            verification   TEXT NOT NULL DEFAULT 'self_reported',
            status         TEXT NOT NULL DEFAULT 'pending',
            attempts       SMALLINT NOT NULL DEFAULT 0,
            note           TEXT NOT NULL DEFAULT '',
            scored_at      TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, kind, entry_id, call_id, horizon_days)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgment_scores_user "
        "ON judgment_scores (user_id, status, exit_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgment_scores_due "
        "ON judgment_scores (status, exit_date)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS judgment_scores")
