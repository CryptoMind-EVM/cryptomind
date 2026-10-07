"""user_agent_configs：user × agent 的 per-agent 設定（c042，Model Mixer Step 1）

設計：docs/plans/2026-09-02-model-mixer-step1-schema.md（DANNY 2026-09-02 拍板方案 B）
上游：docs/plans/2026-09-02-model-mixer-design.md §8／§9 Step 1

- ``user_agent_configs``：user × agent → model_selection（JSONB：
  ``{"provider": "...", "model": "..."}``，NULL = 官方預設）；
  ``tools`` / ``skills`` 為 Step 2／Step 4 預留欄，本次不接線。
- ``agent_id`` 刻意不設 FK／CHECK：agent 目錄是檔案制（ProfileCatalog），
  DB 硬編清單會讓每個新 agent 都要多一次遷移；由 API 層驗證。

Idempotent（``CREATE TABLE IF NOT EXISTS``），並由 schema.py 的
``create_user_agent_configs_table`` 在啟動時自癒（c023／c041 先例：
表本體以 init_db 保底，避免生產 DuplicateTable）。

Rollback：downgrade 直接 DROP TABLE——僅損失新功能資料（per-agent 模型
指定），既有資料零影響（提案 §8 回滾 runbook：先回滾部署再降級 DB）。
"""

from alembic import op

revision = "c042"
down_revision = "c041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_agent_configs (
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            model_selection JSONB,
            tools JSONB NOT NULL DEFAULT '[]',
            skills JSONB NOT NULL DEFAULT '[]',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            PRIMARY KEY (user_id, agent_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_agent_configs")
