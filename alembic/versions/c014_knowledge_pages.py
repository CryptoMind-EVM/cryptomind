"""knowledge_pages

Revision ID: c014
Revises: c013
Create Date: 2026-07-25

Create the ``knowledge_pages`` table for LLM Wiki knowledge compounding
(方向③ — Karpathy LLM Wiki pattern).

Stores valuable analyses as reusable knowledge pages with cross-references,
so the next time a similar question is asked, the agent retrieves the prior
synthesis instead of re-deriving from scratch. This makes the agent genuinely
"stronger over time" — knowledge compounds.

- ``body_tsv`` STORED TSVECTOR + GIN index for FTS retrieval (mirrors
  ``task_experiences`` — no vector DB needed)
- ``quality_score``: heuristic capture score + user-promote can raise it
- ``access_count``: retrieval touch (popular knowledge surfaces first)
- ``promoted``: user-curated pages (vs auto-captured candidates)

Idempotent, also created by ``create_knowledge_tables`` at startup.
"""

from alembic import op


revision = "c014"
down_revision = "c013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_pages (
            id              BIGSERIAL PRIMARY KEY,
            owner_user_id   TEXT NOT NULL,
            title           TEXT NOT NULL,
            body            TEXT NOT NULL,
            source_query    TEXT,
            source_session_id TEXT,
            tags            TEXT[],
            quality_score   REAL DEFAULT 0.0,
            access_count    INTEGER NOT NULL DEFAULT 0,
            promoted        BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW(),
            body_tsv        TSVECTOR GENERATED ALWAYS AS (
                to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(body, ''))
            ) STORED
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_kp_owner ON knowledge_pages(owner_user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_kp_promoted ON knowledge_pages(owner_user_id, promoted) "
        "WHERE promoted = TRUE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_kp_tsv ON knowledge_pages USING GIN(body_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_pages CASCADE")
