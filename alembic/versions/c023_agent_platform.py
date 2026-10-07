"""agent_platform_tables

Revision ID: c023
Revises: c022
Create Date: 2026-08-14

Agent platform Phase 2/3 tables（design §12／impl plan Part B、D3）：
- ``user_agent_presets``：使用者 Agent Preset（partial unique default index）
- ``user_preset_skill_bindings``：preset ↔ skill 綁定（cascade on preset delete）
- ``user_preset_tool_preferences``：preset 專屬 tool 覆寫（僅進階覆寫才建列）
- ``user_favorites``：探索介面收藏（Priority 1 輕量標記）
- ``sessions`` 加 ``agent_preset_id`` / ``agent_config_snapshot``（nullable、無 FK，
  preset 刪除後舊對話靠 snapshot 續讀）

Schema mirrors the ORM models at ``core/orm/models.py``. Idempotent
(``CREATE TABLE IF NOT EXISTS`` / ``ADD COLUMN IF NOT EXISTS``), also
self-healed by ``create_agent_platform_tables`` +
``reconcile_agent_platform_columns`` in ``core/database/schema.py`` at startup.
"""

from alembic import op


revision = "c023"
down_revision = "c022_action_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_agent_presets (
            preset_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'single',
            agent_ids TEXT[] NOT NULL,
            analysis_mode TEXT NOT NULL DEFAULT 'quick',
            action_policy TEXT NOT NULL DEFAULT 'read_only',
            capability_overrides JSONB NOT NULL DEFAULT '{}',
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            config_version TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT ck_preset_mode_valid CHECK (mode IN ('single','auto','team')),
            CONSTRAINT ck_preset_analysis_mode_valid CHECK (analysis_mode IN ('quick','verified','research')),
            CONSTRAINT ck_preset_action_policy_valid CHECK (action_policy IN ('read_only','confirm_actions')),
            CONSTRAINT ck_preset_name_len CHECK (char_length(name) BETWEEN 1 AND 50)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_agent_presets_user "
        "ON user_agent_presets(user_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_agent_presets_default "
        "ON user_agent_presets(user_id) WHERE is_default"
    )

    op.execute(
        """
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
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_preset_skill_bindings_user "
        "ON user_preset_skill_bindings(user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preset_tool_preferences (
            preset_id TEXT NOT NULL REFERENCES user_agent_presets(preset_id) ON DELETE CASCADE,
            tool_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            PRIMARY KEY (preset_id, tool_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_preset_tool_prefs_user "
        "ON user_preset_tool_preferences(user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_favorites (
            fav_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            source_url TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT uq_user_favorite UNIQUE (user_id, item_type, item_id),
            CONSTRAINT ck_favorite_item_type_valid CHECK (item_type IN ('manifund_project','manifund_user'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_favorites_user ON user_favorites(user_id)"
    )

    # sessions 擴充（nullable、無 FK）
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS agent_preset_id TEXT")
    op.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS agent_config_snapshot JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS agent_config_snapshot")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS agent_preset_id")
    op.execute("DROP TABLE IF EXISTS user_favorites CASCADE")
    op.execute("DROP TABLE IF EXISTS user_preset_tool_preferences CASCADE")
    op.execute("DROP TABLE IF EXISTS user_preset_skill_bindings CASCADE")
    op.execute("DROP TABLE IF EXISTS user_agent_presets CASCADE")
