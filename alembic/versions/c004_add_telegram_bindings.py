"""add_telegram_bindings

Revision ID: c004_add_telegram_bindings
Revises: c003_drop_pi_columns
Create Date: 2026-06-18

Add ``telegram_bindings`` table to link Telegram users to existing
platform accounts. This enables the Telegram Bot integration where
users must first register on the web app (BYOK model) and then bind
their Telegram account via a short-lived link token.

Schema:
- telegram_id BIGINT PRIMARY KEY  (Telegram numeric user id, stable)
- user_id     TEXT NOT NULL       (FK -> users.user_id, CASCADE on delete)
- username    TEXT                (Telegram @username, nullable)
- first_name  TEXT                (Telegram display name, nullable)
- linked_at   TIMESTAMPTZ DEFAULT NOW()
- last_used_at TIMESTAMPTZ         (updated on each bot interaction)
- UNIQUE(user_id)                 (one platform account ↔ one Telegram account)
"""

from alembic import op


revision = "c004_add_telegram_bindings"
down_revision = "c003_drop_pi_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_bindings (
            telegram_id BIGINT PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT,
            first_name TEXT,
            linked_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            last_used_at TIMESTAMP WITH TIME ZONE,
            UNIQUE(user_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_telegram_bindings_user "
        "ON telegram_bindings(user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS telegram_bindings")
