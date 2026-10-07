"""add_user_saved_models_table

同 provider 綁第二個模型不再覆蓋第一個：user_saved_models 記錄
「這個 provider 保存過哪些模型」。金鑰仍一 provider 一把
（user_api_keys），使用中的模型仍是 user_api_keys.model_selection。

Revision ID: c009
Revises: c008
Create Date: 2026-07-12
"""

from alembic import op


revision = "c009"
down_revision = "c008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_saved_models (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_user_saved_model UNIQUE (user_id, provider, model),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_saved_models_user
        ON user_saved_models (user_id)
        """
    )
    # 既有綁定回填：每個 provider 目前使用中的模型就是清單的第一筆
    op.execute(
        """
        INSERT INTO user_saved_models (user_id, provider, model)
        SELECT user_id, provider, model_selection
        FROM user_api_keys
        WHERE model_selection IS NOT NULL
          AND model_selection <> ''
          AND key_kind = 'llm'
        ON CONFLICT (user_id, provider, model) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_saved_models")
