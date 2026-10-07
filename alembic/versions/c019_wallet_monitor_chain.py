"""wallet_monitor chain field

Revision ID: c019
Revises: c018
Create Date: 2026-08-11

路線 C（docs/plans/2026-08-11-wallet-monitor-multichain-design.md）：
為 monitored_wallets JSONB 物件加 chain 鍵。既有資料回填 chain="ton"。
純 JSONB shape 擴充——不新增 SQL 欄位，down migration 為 noop（移除 chain 鍵
不破壞舊碼，舊碼忽略此鍵）。
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "c019"
down_revision = "c018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """回填既有 monitored_wallets 物件加 chain="ton"。"""
    # users.wallet_alert_settings 是 JSONB，monitored_wallets 是其中的陣列。
    # 用 jsonb_set 逐一回填；若無 wallet_alert_settings 或 monitored_wallets 則 noop。
    op.execute(
        """
        UPDATE users
        SET wallet_alert_settings = jsonb_set(
            COALESCE(wallet_alert_settings, '{}'::jsonb),
            '{monitored_wallets}',
            COALESCE(
                (
                    SELECT jsonb_agg(
                        CASE
                            WHEN elem ? 'chain' THEN elem
                            ELSE elem || '{"chain": "ton"}'::jsonb
                        END
                    )
                    FROM jsonb_array_elements(
                        wallet_alert_settings->'monitored_wallets'
                    ) AS elem
                ),
                wallet_alert_settings->'monitored_wallets',
                '[]'::jsonb
            )
        )
        WHERE wallet_alert_settings ? 'monitored_wallets'
          AND jsonb_typeof(wallet_alert_settings->'monitored_wallets') = 'array'
        """
    )


def downgrade() -> None:
    """移除 chain 鍵（noop-safe：舊碼忽略此鍵）。"""
    op.execute(
        """
        UPDATE users
        SET wallet_alert_settings = jsonb_set(
            wallet_alert_settings,
            '{monitored_wallets}',
            COALESCE(
                (
                    SELECT jsonb_agg(elem - 'chain')
                    FROM jsonb_array_elements(
                        wallet_alert_settings->'monitored_wallets'
                    ) AS elem
                ),
                wallet_alert_settings->'monitored_wallets',
                '[]'::jsonb
            )
        )
        WHERE wallet_alert_settings ? 'monitored_wallets'
          AND jsonb_typeof(wallet_alert_settings->'monitored_wallets') = 'array'
        """
    )
