"""add_language_to_users

Revision ID: c007
Revises: c006
Create Date: 2026-06-30

Add ``language`` to ``users`` so a logged-in user's UI language preference is
stored server-side (syncs across devices, and lets a linked Telegram account
reuse it). NULL = no explicit preference → frontend falls back to localStorage
/ browser detection, so existing users keep working unchanged.

Note: revision ID kept short (``c007``) — see c005 note: alembic_version.version_num
defaults to VARCHAR(32).
"""

from alembic import op


revision = "c007"
down_revision = "c006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS language")
