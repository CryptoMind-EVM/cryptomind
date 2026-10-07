"""trust_score

Revision ID: c015
Revises: c014
Create Date: 2026-08-08

Create the ``user_trust_scores`` table + cache columns on ``users`` for the
trust_score feature (docs/plans/2026-08-08-trust-score-retention-design.md).

This makes the existing ephemeral trust_score (computed by
``core/identity/trust.py:assess_identity_trust``) persistent and queryable:

- ``user_trust_scores``: append-only history of every recomputation, with the
  full signal breakdown JSON for audit/calibration. The latest row per user is
  the current score.
- ``users.trust_score`` / ``users.trust_tier`` / ``users.trust_score_updated_at``:
  denormalized cache columns so tier lookups (e.g. for badges) don't need a
  join.

Idempotent (``IF NOT EXISTS``), also created by ``create_trust_score_tables``
at startup to coexist with the runtime ``init_db()`` schema bootstrap.
"""

from alembic import op


revision = "c015"
down_revision = "c014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_trust_scores (
            id                  BIGSERIAL PRIMARY KEY,
            user_id             TEXT NOT NULL,
            trust_score         INTEGER NOT NULL DEFAULT 0,
            tier                TEXT NOT NULL DEFAULT 'anonymous',
            signal_breakdown    JSONB NOT NULL DEFAULT '{}'::jsonb,
            recompute_reason    TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_uts_user ON user_trust_scores(user_id, created_at DESC)"
    )
    # 快取欄位（denormalized）——徽章查詢不必 join。
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS trust_score INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS trust_tier TEXT NOT NULL DEFAULT 'anonymous'"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS trust_score_updated_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_trust_scores CASCADE")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS trust_score_updated_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS trust_tier")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS trust_score")
