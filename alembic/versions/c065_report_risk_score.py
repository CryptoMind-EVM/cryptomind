"""檢舉附上內容檢查的風險分數，後台照危險程度排（c065）

2026-10-01：發文內容檢查（core/moderation）的模型也拿來幫檢舉排序——被檢舉的內容先打分數，
管理員先看最危險的。只有後台用；論壇的社群投票佇列不顯示（不影響投票的人）。
risk_score：規則命中（索取助記詞、加倍返還…）＝1.0，否則模型詐騙分數；檢查服務不在就是 NULL。
私訊只在被檢舉時才打分數，不主動掃。

Rollback：``alembic downgrade c064``（拿掉兩組欄位，後台退回照時間排）。
"""

from alembic import op

revision = "c065"
down_revision = "c064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("content_reports", "dm_reports"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS risk_score REAL")
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS risk_category TEXT")


def downgrade() -> None:
    for table in ("content_reports", "dm_reports"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS risk_category")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS risk_score")
