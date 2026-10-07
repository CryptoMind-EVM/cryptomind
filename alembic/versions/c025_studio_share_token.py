"""proposal_studio_share_token

Revision ID: c025
Revises: c024
Create Date: 2026-08-16

提案工作台軌跡分享（design 2026-08-16 硬邊界 #5／Phase 2）：
- ``proposal_drafts.share_token``： nullable；NULL = 未分享（預設私有）
- partial unique index（非 NULL 才參與唯一）

Idempotent；self-healed by ``create_agent_platform_tables`` at startup。
"""

from alembic import op


revision = "c025"
down_revision = "c024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE proposal_drafts ADD COLUMN IF NOT EXISTS share_token TEXT")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_proposal_drafts_share_token "
        "ON proposal_drafts(share_token) WHERE share_token IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_proposal_drafts_share_token")
    op.execute("ALTER TABLE proposal_drafts DROP COLUMN IF EXISTS share_token")
