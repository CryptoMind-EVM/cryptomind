"""add_display_name_to_users

Revision ID: c010
Revises: c009
Create Date: 2026-07-21

Add ``display_name`` + ``display_name_updated_at`` to ``users`` so a logged-in
user can set a personalized nickname shown in chat greetings and the agent's
system prompt (Trustworthy AI Hackathon — Principal pillar: the agent knows
who it is talking to).

- ``display_name``: user-chosen nickname. NULL = never set → frontend/agent
  falls back to ``username`` (e.g. ``TON_<6hex>``), so existing users keep
  working unchanged.
- ``display_name_updated_at``: timestamp of the last rename; enforces the
  24h free-rename cooldown. NULL = never renamed → no cooldown.

This mirrors ``c007_add_user_language``: idempotent ``ADD COLUMN IF NOT EXISTS``
so it is safe to re-run, and the column is also added by
``reconcile_user_tables`` at startup so the feature cannot be silently blocked
by an unapplied migration.

Note: revision ID kept short (``c010``) — see c005 note: alembic_version.version_num
defaults to VARCHAR(32).
"""

from alembic import op


revision = "c010"
down_revision = "c009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name_updated_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS display_name_updated_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS display_name")
