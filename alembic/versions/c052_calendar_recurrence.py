"""行事曆重複事件（c052）

使用者自訂事件可以每週／每月／每年重複（房租、信用卡繳款、訂閱扣款）。
只存一列：``event_date`` 是錨點日，讀取時在區間內展開（core/daily_brief/store.py
``expand_occurrences``）；``recurrence_until`` 選填，含當天。

兩個欄位都有預設值，既有資料不用回填（全部視為不重複）。

Rollback：``alembic downgrade c051``（DROP COLUMN；重複事件會變回只剩錨點那一次）。
"""

from alembic import op

revision = "c052"
down_revision = "c051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_calendar_events ADD COLUMN IF NOT EXISTS "
        "recurrence TEXT NOT NULL DEFAULT 'none'"
    )
    op.execute(
        "ALTER TABLE user_calendar_events ADD COLUMN IF NOT EXISTS "
        "recurrence_until DATE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE user_calendar_events DROP COLUMN IF EXISTS recurrence_until"
    )
    op.execute("ALTER TABLE user_calendar_events DROP COLUMN IF EXISTS recurrence")
