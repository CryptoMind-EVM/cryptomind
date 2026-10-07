"""wallet_alert_settings

Revision ID: c018
Revises: c016
Create Date: 2026-08-08

Add ``users.wallet_alert_settings`` JSONB column for the wallet-monitor
dashboard (docs/plans/2026-08-08-wallet-monitor-dashboard-design.md).

Stores per-user wallet monitoring + alert configuration:
    {
      "monitored_wallets": ["EQ..."],
      "alerts": {
        "incoming":  {"enabled": true,  "min_amount_ton": 10},
        "outgoing":  {"enabled": true,  "min_amount_ton": 10},
        "scam":      {"enabled": true},
        "large_out": {"enabled": false, "threshold_ton": 500},
        "custom":    []
      },
      "channels": {"in_app": true, "telegram": true, "discord": false}
    }

v1 minimal (YAGNI): single JSONB column on users. Upgrade to a dedicated
``wallet_monitors`` table when multi-wallet/rule complexity demands it.

Idempotent. Also reconciled at startup by ``reconcile_user_tables``.
"""

from alembic import op


revision = "c018"
down_revision = "c016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "wallet_alert_settings JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS wallet_alert_settings")
