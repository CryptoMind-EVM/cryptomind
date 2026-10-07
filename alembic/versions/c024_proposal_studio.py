"""proposal_studio_tables

Revision ID: c024
Revises: c023
Create Date: 2026-08-16

提案工作台（募資者模式，design 2026-08-16）：
- ``proposal_drafts``：草稿主檔（同人同名唯一；draft/exported/archived）
- ``draft_versions``：不可變版本軌跡（只插入；source = human/ai_applied/autosave；
  char_delta 記與前版字數差）

Schema mirrors the ORM models at ``core/orm/models.py``. Idempotent
(``CREATE TABLE IF NOT EXISTS``), also self-healed by
``create_agent_platform_tables`` in ``core/database/schema.py`` at startup.
Down = drop 兩表（versions 級聯），回滾前需確認資料可棄（design 載明）。
"""

from alembic import op


revision = "c024"
down_revision = "c023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS proposal_drafts (
            draft_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            cause TEXT,
            target_usd NUMERIC,
            status TEXT NOT NULL DEFAULT 'draft',
            current_version_no INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT uq_user_draft_title UNIQUE (user_id, title),
            CONSTRAINT ck_draft_status_valid CHECK (status IN ('draft','exported','archived'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposal_drafts_user ON proposal_drafts(user_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS draft_versions (
            version_id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL REFERENCES proposal_drafts(draft_id) ON DELETE CASCADE,
            version_no INTEGER NOT NULL,
            content_md TEXT NOT NULL,
            change_summary TEXT,
            source TEXT NOT NULL,
            char_delta INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT uq_draft_version_no UNIQUE (draft_id, version_no),
            CONSTRAINT ck_draft_version_source_valid CHECK (source IN ('human','ai_applied','autosave'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_draft_versions_draft ON draft_versions(draft_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS draft_versions")
    op.execute("DROP TABLE IF EXISTS proposal_drafts")
