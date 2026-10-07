"""add_active_session_to_telegram_bindings

Revision ID: c005
Revises: c004_add_telegram_bindings
Create Date: 2026-06-20

Add ``active_session_id`` to ``telegram_bindings`` so a Telegram user can
switch which chat session the bot reads/writes (enabling /sessions in the
bot). When NULL the bot falls back to the default rolling session
``tg:{telegram_id}`` — so existing bindings keep working unchanged.

Note: revision ID shortened from ``c005_add_active_session_to_telegram`` (35 chars)
to ``c005`` because alembic_version.version_num defaults to VARCHAR(32), causing
``value too long for type character varying(32)`` on the version stamp UPDATE
and rolling back the entire migration transaction.
"""

from alembic import op


revision = "c005"
down_revision = "c004_add_telegram_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE telegram_bindings ADD COLUMN IF NOT EXISTS active_session_id TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE telegram_bindings DROP COLUMN IF EXISTS active_session_id")
