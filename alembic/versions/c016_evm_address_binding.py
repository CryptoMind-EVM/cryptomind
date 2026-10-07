"""evm_address_binding

Revision ID: c016
Revises: c015
Create Date: 2026-08-08

Add ``users.evm_address`` + ``users.evm_bound_at`` for Human Passport
integration (docs/plans/2026-08-08-evm-address-binding-design.md).

TON users bind an EVM address (with personal_sign proof of ownership) so
that ``collect_passport_stamps`` can query the Human Passport Scorer API,
which only accepts EVM (0x...) addresses.

Idempotent. Also reconciled at startup by ``reconcile_user_tables``.
"""

from alembic import op


revision = "c016"
down_revision = "c015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS evm_address TEXT"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS evm_bound_at TIMESTAMPTZ"
    )
    # 一個 EVM 地址只能綁一個 user（防蹭分）。部分唯一索引（只對有綁定的列生效）。
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_evm_address_unique "
        "ON users(evm_address) WHERE evm_address IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_evm_address_unique")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS evm_bound_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS evm_address")
