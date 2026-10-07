"""funder_qa_telemetry_prospect

Revision ID: c027
Revises: c026
Create Date: 2026-08-17

三件套（design 2026-08-17，DANNY Approved「全部處理完成」）：
- ``draft_versions`` 補輸入指紋四欄（typed_chars/paste_events/pasted_chars/edit_seconds）
- ``prospect_logs``：前瞻評測記錄（ai-review 成功即寫，事前預測可對照實際結果）

Idempotent；self-healed by ``create_agent_platform_tables`` at startup。
"""

from alembic import op


revision = "c027"
down_revision = "c026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("typed_chars", "paste_events", "pasted_chars", "edit_seconds"):
        op.execute(
            f"ALTER TABLE draft_versions ADD COLUMN IF NOT EXISTS {col} "
            "INTEGER NOT NULL DEFAULT 0"
        )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS prospect_logs (
            log_id TEXT PRIMARY KEY,
            project_slug TEXT NOT NULL,
            cause TEXT,
            stage_at_eval TEXT,
            content_snapshot JSONB NOT NULL,
            evaluation_json JSONB NOT NULL,
            language TEXT NOT NULL,
            model TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_prospect_logs_slug ON prospect_logs(project_slug)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS prospect_logs")
    for col in ("edit_seconds", "pasted_chars", "paste_events", "typed_chars"):
        op.execute(f"ALTER TABLE draft_versions DROP COLUMN IF EXISTS {col}")
