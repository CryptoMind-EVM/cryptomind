"""add_user_feedback_table

Revision ID: c008
Revises: c007
Create Date: 2026-07-02
"""

from alembic import op


revision = "c008"
down_revision = "c007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_feedback (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT,
            message TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_feedback_user_id
        ON user_feedback (user_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_feedback_created_at
        ON user_feedback (created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS user_feedback')
