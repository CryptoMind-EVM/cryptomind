"""user_facts_curation_columns

Revision ID: c013
Revises: c012
Create Date: 2026-07-25

Add ``created_at``, ``last_accessed``, ``access_count`` columns to
``user_facts`` to support Hermes-style memory curation (方向②):

- ``created_at``: when the fact was first inserted (age-based pruning)
- ``last_accessed``: last time the fact was read into a prompt (recency)
- ``access_count``: how often the fact was used (popularity)

These enable: read-time budget ranking (popular + recent facts kept),
stale-fact pruning (old + never-accessed facts dropped), preventing the
injected memory from growing unbounded and bloating the prompt / breaking
prefix cache.

Idempotent (``ADD COLUMN IF NOT EXISTS``), also self-healed by
``reconcile_user_facts_columns`` at startup.

Note: existing rows get sensible defaults (created_at=NOW, access_count=0)
so curation starts from a clean baseline without backfilling.
"""

from alembic import op


revision = "c013"
down_revision = "c012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
    )
    op.execute(
        "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
        "last_accessed TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
    )
    op.execute(
        "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
        "access_count INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE user_facts DROP COLUMN IF EXISTS access_count")
    op.execute("ALTER TABLE user_facts DROP COLUMN IF EXISTS last_accessed")
    op.execute("ALTER TABLE user_facts DROP COLUMN IF EXISTS created_at")
