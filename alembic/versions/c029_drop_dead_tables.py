"""drop_dead_tables_and_columns

Revision ID: c029
Revises: c028
Create Date: 2026-08-18

死表/死欄清理（DB 欄位使用率審計 2026-08-17；DANNY 授權「直接規劃進行全方面優化」）：

- DROP TABLE ``learned_skills``——LearnedSkillStore 已於 c014 memory convergence
  移除，表自 2026-07-31 起無任何讀寫。
- DROP TABLE ``user_preset_skill_bindings`` / ``user_preset_tool_preferences``
  ——c023 建表後從未實作讀寫（preset 主表 user_agent_presets 不受影響）。
- DROP COLUMN ``sessions.agent_preset_id`` / ``sessions.agent_config_snapshot``
  ——c023 宣稱「對話建立時鎖定 preset」但從未實作（記憶體中即用 agent_presets）。
- DROP COLUMN ``user_api_keys.last_used_at``——從未寫入（repo 無此欄操作）。
- DROP COLUMN ``dm_message_deletions.deleted_at``——僅 DB default 填值，無讀取。

全部經全 repo 雙形態 grep（ORM attribute + SQL 字串）驗證零引用。
Idempotent（IF EXISTS）；downgrade 重建空結構（資料不可復原——本來就沒有資料）。
"""

from alembic import op


revision = "c029"
down_revision = "c028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS learned_skills")
    op.execute("DROP TABLE IF EXISTS user_preset_skill_bindings")
    op.execute("DROP TABLE IF EXISTS user_preset_tool_preferences")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS agent_preset_id")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS agent_config_snapshot")
    op.execute("ALTER TABLE user_api_keys DROP COLUMN IF EXISTS last_used_at")
    op.execute("ALTER TABLE dm_message_deletions DROP COLUMN IF EXISTS deleted_at")


def downgrade() -> None:
    # 重建空結構供回滾相容；原表本無資料，無資料可還原。
    op.execute(
        "ALTER TABLE dm_message_deletions ADD COLUMN IF NOT EXISTS deleted_at "
        "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    )
    op.execute(
        "ALTER TABLE user_api_keys ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP"
    )
    op.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS agent_preset_id TEXT"
    )
    op.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS agent_config_snapshot JSONB"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_preset_tool_preferences (
            preset_id TEXT NOT NULL REFERENCES user_agent_presets(preset_id) ON DELETE CASCADE,
            tool_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            PRIMARY KEY (preset_id, tool_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_preset_skill_bindings (
            preset_id TEXT NOT NULL REFERENCES user_agent_presets(preset_id) ON DELETE CASCADE,
            skill_name TEXT NOT NULL,
            skill_type TEXT NOT NULL,
            user_id TEXT NOT NULL,
            is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            PRIMARY KEY (preset_id, skill_name, skill_type),
            CONSTRAINT ck_preset_skill_type_valid CHECK (skill_type IN ('official','custom'))
        )
    """)
