"""記憶分層：user_facts_private 新表＋user_memory agent 維度（c043，Step 4）

設計：docs/plans/2026-09-03-model-mixer-step4-memory.md（DANNY 2026-09-03
授權自決：「DB 你就自己設計好」）

- **新表 ``user_facts_private``**：agent 私有事實層。共享表 ``user_facts``
  完全不動（免 constraint 手術、生產熱表零風險）；私有層實體分離讓隔離
  邊界可直接審計。PK 語義：``UNIQUE(user_id, agent_id, key)``。
- ``user_memory`` 加 ``agent_id``＋``scope``（建欄不接線——Step 5 私有
  blob 起用，比照 c042 tools/skills 先例）。舊 UNIQUE(user_id, session_id,
  memory_type) **維持原樣**：它同時是 write_long_term／write_compact_state
  的 ON CONFLICT arbiter，換成 expression index 會讓 Postgres 推導不出
  arbiter → consolidation 靜默死亡。它確有 NULL-distinct 缺陷，但那個手術
  要跟 arbiter 一起換，留給 Step 5 真正寫 agent blob 時處理（見下方 upgrade
  內註解）。
- 回填：**零資料搬移**——既有資料全在共享層，語義不變。
- ``agent_id`` 刻意不設 FK／CHECK（同 c042：agent 目錄檔案制）。

Rollback：downgrade DROP 新表／新欄——僅損失私有層資料，共享層零影響。
回滾 runbook：先回滾部署 → ``alembic downgrade c042``。
"""

from alembic import op

revision = "c043"
down_revision = "c042"
branch_labels = None
depends_on = None

_USER_MEMORY = "user_memory"


def upgrade() -> None:
    # ── 新表：user_facts_private（agent 私有事實層） ────────────────────
    #
    # 2026-09-05 冪等化。原本用 op.create_table，而 core/database/schema.py 的
    # create_user_facts_private_table() 用 CREATE TABLE IF NOT EXISTS 在 app
    # 啟動時就把同一張表建好了——alembic 隨後撞 DuplicateTable，整個 upgrade
    # 中止。production 因此卡在 c042 好幾週，而 entrypoint 是 best-effort，
    # gunicorn 照常啟動，服務看起來健康。
    #
    # 兩邊的欄位、約束名（uq_user_fact_private）、索引名都相同，所以改成
    # IF NOT EXISTS 的最終狀態與原版逐字一致；已經跑過 c043 的環境不受影響
    # （alembic 不會重跑）。
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_facts_private (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            confidence TEXT DEFAULT 'high',
            source_turn INTEGER,
            category TEXT DEFAULT 'fact',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

            CONSTRAINT uq_user_fact_private UNIQUE (user_id, agent_id, key)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_fact_private_agent "
        "ON user_facts_private(user_id, agent_id)"
    )

    # ── user_memory：agent_id＋scope（建欄不接線） ───────────────────────
    # 同樣冪等化：reconcile 對既有表只做 CREATE TABLE IF NOT EXISTS（no-op，
    # 不補欄位），所以這兩欄只能靠遷移加；但遷移一旦被前面的 DuplicateTable
    # 擋住就永遠加不上——而 reconcile 又會去建引用 agent_id 的索引並失敗。
    # 兩套自癒系統互鎖。
    op.execute(f"ALTER TABLE {_USER_MEMORY} ADD COLUMN IF NOT EXISTS agent_id TEXT")
    op.execute(
        f"ALTER TABLE {_USER_MEMORY} ADD COLUMN IF NOT EXISTS "
        "scope TEXT NOT NULL DEFAULT 'shared'"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_user_memory_agent "
        f"ON {_USER_MEMORY}(user_id, agent_id)"
    )
    # 刻意**不**動既有 UNIQUE(user_id, session_id, memory_type)：它同時是
    # write_long_term／write_compact_state 的 ON CONFLICT arbiter（review P0
    # 教訓——換成 expression index 會讓 Postgres 無法推導 arbiter →
    # consolidation 靜默死亡）。agent 維度唯一性手術留給 Step 5 真正寫
    # agent blob 時連同 arbiter 一起換。


def downgrade() -> None:
    # 同 upgrade 冪等化：upgrade 若在中途失敗，這些物件可能只建了一半，
    # 非冪等的 drop 會讓回滾本身也炸掉（然後就兩邊都動不了）。
    op.execute("DROP INDEX IF EXISTS idx_user_memory_agent")
    op.execute(f"ALTER TABLE {_USER_MEMORY} DROP COLUMN IF EXISTS scope")
    op.execute(f"ALTER TABLE {_USER_MEMORY} DROP COLUMN IF EXISTS agent_id")

    op.execute("DROP INDEX IF EXISTS idx_user_fact_private_agent")
    op.execute("DROP TABLE IF EXISTS user_facts_private")
