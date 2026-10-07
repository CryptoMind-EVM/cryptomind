"""add_jwt_keys_table

Revision ID: c001_add_jwt_keys_table
Revises: 92a35ecee1cf
Create Date: 2026-03-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "c001_add_jwt_keys_table"
down_revision: Union[str, Sequence[str], None] = "b003_stamp_existing_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create jwt_keys table for DB-backed JWT key rotation (idempotent).

    Raw IF NOT EXISTS so the migration is safe to (re-)run against a DB
    whose schema was bootstrapped by init_db() — see 92a35ecee1cf / b003.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS jwt_keys (
            id TEXT PRIMARY KEY,
            value_encrypted TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            is_primary BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            deprecated_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT ck_jwt_key_status
                CHECK (status IN ('active', 'deprecated', 'expired'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_jwt_keys_status ON jwt_keys(status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_jwt_keys_is_primary "
        "ON jwt_keys(is_primary)"
    )


def downgrade() -> None:
    """Drop jwt_keys table."""
    op.drop_index("idx_jwt_keys_is_primary", table_name="jwt_keys")
    op.drop_index("idx_jwt_keys_status", table_name="jwt_keys")
    op.drop_table("jwt_keys")
