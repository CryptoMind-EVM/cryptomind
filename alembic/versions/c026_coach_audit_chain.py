"""coach_audit_chain

Revision ID: c026
Revises: c025
Create Date: 2026-08-16

教練對話稽核鏈（design 2026-08-16 A 方案，DANNY 核可「我認為A」）：
- ``coach_exchanges``：每次教練看稿的完整往返（問題＋過濾後回應），不可變
- ``suggestion_outcomes``：建議處置（adopted 掛 version_no / dismissed）

採納的標籤從「自願聲稱」升級為「可交叉驗證的鏈路」：
採納文字可對照事前存檔的 AI 回應，且進稿時間在後。

Idempotent；self-healed by ``create_agent_platform_tables`` at startup。
"""

from alembic import op


revision = "c026"
down_revision = "c025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS coach_exchanges (
            exchange_id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL REFERENCES proposal_drafts(draft_id) ON DELETE CASCADE,
            question TEXT NOT NULL DEFAULT '',
            response_json JSONB NOT NULL,
            language TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_coach_exchanges_draft ON coach_exchanges(draft_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS suggestion_outcomes (
            outcome_id TEXT PRIMARY KEY,
            exchange_id TEXT NOT NULL REFERENCES coach_exchanges(exchange_id) ON DELETE CASCADE,
            quote TEXT NOT NULL,
            outcome TEXT NOT NULL,
            version_no INTEGER,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT ck_suggestion_outcome_valid CHECK (outcome IN ('adopted','dismissed'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_suggestion_outcomes_exchange "
        "ON suggestion_outcomes(exchange_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS suggestion_outcomes")
    op.execute("DROP TABLE IF EXISTS coach_exchanges")
