"""預防性索引補進 alembic（schema.py 已有，補單一真相源）

2026-08-23 索引審計把三個預防性索引建在 core/database/schema.py（每次
boot 的 create_all_tables 會建）——alembic 沒有對應 revision，環境若
只跑 alembic（或 DB user 無 CREATE EXTENSION 權限被 per-step 吞掉）
會缺索引：trade_journal note ILIKE 搜尋全表掃描、user_facts 治理鏈/
過期清理無索引。補 c036 對齊（冪等，IF NOT EXISTS）。
"""
from alembic import op

revision = "c036"
down_revision = "c035"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_note_trgm "
        "ON trade_journal USING gin (note gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_facts_supersedes "
        "ON user_facts(user_id, supersedes_id) WHERE supersedes_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_facts_valid_until "
        "ON user_facts(valid_until) WHERE valid_until IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_trade_journal_note_trgm")
    op.execute("DROP INDEX IF EXISTS idx_user_facts_supersedes")
    op.execute("DROP INDEX IF EXISTS idx_user_facts_valid_until")
