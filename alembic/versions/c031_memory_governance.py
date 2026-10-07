"""memory_governance — user_facts 治理欄位 + user_skill_revisions 版本表

Revision ID: c031
Revises: c030
Create Date: 2026-08-21

記憶治理與版本（docs/plans/2026-08-21-memory-governance-design.md，
DANNY 2026-08-21 Approve）。金融平台差異化：時效/溯源/衝突/回滾。

user_facts 新欄位（全部 DEFAULT——既有資料行為零變化）：
- valid_from / valid_until——時間有效期（NULL=永久，如投資偏好）
- source_query——觸發此事實的使用者原句（source_turn 只有輪次號）
- supersedes_id——被本條取代的舊事實 id（衝突審計鏈）
- status——active / superseded / expired / archived
- verified_at——使用者明確確認時間（NULL=AI 推測）

新表 user_skill_revisions：
- custom skill 每次修改存舊版，支援版本列表/diff/rollback
- edited_via: user（Settings UI）/ agent_hitl（agent 提議＋HITL）/ system
"""

from alembic import op

revision = "c031"
down_revision = "c030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # user_facts 治理欄位
    op.execute(
        "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
        "valid_from TIMESTAMPTZ DEFAULT NOW()"
    )
    op.execute(
        "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
        "valid_until TIMESTAMPTZ DEFAULT NULL"
    )
    op.execute(
        "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
        "source_query TEXT DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
        "supersedes_id INTEGER DEFAULT NULL"
    )
    op.execute(
        "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
        "status VARCHAR(20) DEFAULT 'active'"
    )
    op.execute(
        "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
        "verified_at TIMESTAMPTZ DEFAULT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_facts_status "
        "ON user_facts(user_id, status)"
    )

    # skill 版本表
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_skill_revisions (
            id               SERIAL PRIMARY KEY,
            skill_id         INTEGER NOT NULL,
            revision         INTEGER NOT NULL,
            body             TEXT NOT NULL,
            description      TEXT DEFAULT '',
            trigger_keywords TEXT DEFAULT '',
            edited_via       VARCHAR(20) DEFAULT 'user',
            created_at       TIMESTAMP DEFAULT NOW(),
            UNIQUE(skill_id, revision)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_skill_revisions_skill "
        "ON user_skill_revisions(skill_id)"
    )

    # custom skills 加 revision 欄位
    op.execute(
        "ALTER TABLE user_custom_skills ADD COLUMN IF NOT EXISTS "
        "revision INTEGER NOT NULL DEFAULT 1"
    )


def downgrade() -> None:
    # 治理欄位留著無害；skill_revisions 留著無害（rollback 不做破壞性操作）
    pass
