"""learned_skills_table

Revision ID: c012
Revises: c011
Create Date: 2026-07-25

Create the ``learned_skills`` table. This table was referenced by
``LearnedSkillStore.record_skill`` (called after every successful agent turn)
but had **no DDL anywhere** — writes silently failed in production (caught by
the bare ``except Exception`` in ``_record_experience_background``).

This unblocks 方向① (wiring experience/skill retrieval into the live agent
path) so that learned tool-sequence patterns actually persist and can be
recalled.

Schema mirrors the ORM model at ``core/orm/models.py:LearnedSkill``:
- ``trigger_tsv`` STORED TSVECTOR + GIN index for FTS retrieval
- ``UNIQUE(user_id, trigger_pattern)`` for upsert (success/failure count
  accumulation)
- ``success_count``/``failure_count`` compounding counters

Idempotent (``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``),
also self-healed by ``create_learned_skills_tables`` in
``core/database/schema.py`` at startup.
"""

from alembic import op


revision = "c012"
down_revision = "c011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS learned_skills (
            id              BIGSERIAL PRIMARY KEY,
            user_id         TEXT NOT NULL,
            skill_name      TEXT NOT NULL,
            trigger_pattern TEXT NOT NULL,
            task_family     TEXT NOT NULL,
            tools_sequence  TEXT[],
            agent_used      TEXT NOT NULL DEFAULT '',
            success_count   INTEGER NOT NULL DEFAULT 1,
            failure_count   INTEGER NOT NULL DEFAULT 0,
            last_used_at    TIMESTAMPTZ DEFAULT NOW(),
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            trigger_tsv     TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', trigger_pattern)) STORED,
            CONSTRAINT uq_learned_skill_user_pattern UNIQUE (user_id, trigger_pattern)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ls_user_family ON learned_skills(user_id, task_family)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ls_tsv ON learned_skills USING GIN(trigger_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS learned_skills CASCADE")
