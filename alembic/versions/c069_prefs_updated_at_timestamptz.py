"""資料庫欄位型別對齊程式與正式站：updated_at／created_at 轉成 timestamptz（c069）

2026-10-03 DANNY：換機重建環境時，下面三個欄位會和「程式宣告」及「正式站實際」不一致——
ORM（core/orm/models.py）都宣告 TIMESTAMP(timezone=True)：

- user_llm_preferences.updated_at、user_analysis_preferences.updated_at：正式站是沒時區的
  TIMESTAMP（舊表）。目前無害（SQLAlchemy 對 asyncpg 會把參數明確轉成
  ::TIMESTAMP WITH TIME ZONE 再寫入），但日後有人拿它做時間比較就會踩到
  can't compare offset-naive and offset-aware datetimes。
- price_alerts.created_at：DDL（core/database/schema.py、b003）一直是 TEXT，正式站是 timestamptz
  （當初手動跑過 reconcile_timestamptz；該手動工具已移除，改由 DDL 與本 migration 負責）。從零重建出來會是 text，ORM 讀寫會壞。

做法（冪等）：欄位目前是舊型別才轉；已經是 timestamptz（正式站、core/database/schema.py 新建的庫）
或表不存在就什麼都不做。
- 沒時區 → 有時區：用明確的 ``AT TIME ZONE 'UTC'`` 解讀，不依賴 session 的 TimeZone
  （正式站 DB 時區是 Etc/UTC，現有值本來就是 UTC 時間，不會偏移）。
- text → timestamptz：``::timestamptz``（值是 ISO 字串）。轉不動會直接報錯、migration 失敗，
  不會靜默留下不一致。
兩張偏好表正式站只有 8 列／0 列、只有主鍵索引、沒有 view，轉換是瞬間完成。
Rollback：``alembic downgrade c068``（轉回舊型別；price_alerts.created_at 轉回 text）。
"""

from alembic import op

revision = "c069"
down_revision = "c068"
branch_labels = None
depends_on = None

# (表, 欄位, 舊型別（information_schema.data_type）, 舊→新的轉換式, 新→舊的轉換式, 舊型別的 SQL 名稱)
CONVERSIONS = (
    (
        "user_llm_preferences",
        "updated_at",
        "timestamp without time zone",
        "updated_at AT TIME ZONE 'UTC'",
        "updated_at AT TIME ZONE 'UTC'",
        "TIMESTAMP",
    ),
    (
        "user_analysis_preferences",
        "updated_at",
        "timestamp without time zone",
        "updated_at AT TIME ZONE 'UTC'",
        "updated_at AT TIME ZONE 'UTC'",
        "TIMESTAMP",
    ),
    (
        "price_alerts",
        "created_at",
        "text",
        "created_at::timestamptz",
        "created_at::text",
        "TEXT",
    ),
)

_TZ = "timestamp with time zone"


def _convert_sql(spec: tuple, *, to_tz: bool) -> str:
    """只在欄位目前是相反型別時才轉；表或欄位不存在就什麼都不做。"""
    table, column, old_type, up_expr, down_expr, old_sql = spec
    current = old_type if to_tz else _TZ
    target = "TIMESTAMPTZ" if to_tz else old_sql
    expr = up_expr if to_tz else down_expr
    return f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = '{table}'
                  AND column_name = '{column}'
                  AND data_type = '{current}'
            ) THEN
                ALTER TABLE {table}
                    ALTER COLUMN {column} TYPE {target}
                    USING {expr};
            END IF;
        END $$;
    """


def upgrade() -> None:
    for spec in CONVERSIONS:
        op.execute(_convert_sql(spec, to_tz=True))


def downgrade() -> None:
    for spec in CONVERSIONS:
        op.execute(_convert_sql(spec, to_tz=False))
