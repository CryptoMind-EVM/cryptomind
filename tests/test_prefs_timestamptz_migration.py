"""c069：user_llm_preferences／user_analysis_preferences.updated_at、price_alerts.created_at 轉成 timestamptz。

換機重建時這三欄會和程式（ORM 宣告 timestamptz）、正式站不一致（2026-10-03 從零重建並逐欄比對發現）。
migration 要：
- 冪等（欄位已經是 timestamptz／表不存在就什麼都不做）
- 沒時區的舊值用明確的 AT TIME ZONE 'UTC' 解讀（正式站 DB 時區是 Etc/UTC，值不能偏移）
- 能回滾
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "alembic/versions/c069_prefs_updated_at_timestamptz.py"


def _load():
    spec = importlib.util.spec_from_file_location("c069_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_chain_and_columns():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision = "c069"$', src, re.M)
    assert re.search(r'^down_revision = "c068"$', src, re.M)
    mod = _load()
    assert {(t, c) for t, c, *_ in mod.CONVERSIONS} == {
        ("user_llm_preferences", "updated_at"),
        ("user_analysis_preferences", "updated_at"),
        ("price_alerts", "created_at"),
    }


def test_sql_is_idempotent_and_utc_explicit():
    mod = _load()
    for spec in mod.CONVERSIONS:
        up = mod._convert_sql(spec, to_tz=True)
        down = mod._convert_sql(spec, to_tz=False)
        # 只有欄位目前是相反型別才轉（冪等）
        assert f"data_type = '{spec[2]}'" in up
        assert "data_type = 'timestamp with time zone'" in down
        assert "TYPE TIMESTAMPTZ" in up
        assert re.search(rf"TYPE {spec[5]}\s", down), "回滾要轉回舊型別"
    # 沒時區 → 有時區：不依賴 session 的 TimeZone，明確用 UTC 解讀
    naive_specs = [s for s in mod.CONVERSIONS if s[2] == "timestamp without time zone"]
    assert naive_specs
    for spec in naive_specs:
        assert "AT TIME ZONE 'UTC'" in mod._convert_sql(spec, to_tz=True)


def test_schema_ddl_creates_timestamptz_for_all_three_columns():
    """新建的資料庫（init_db）這三欄就是 timestamptz，migration 在那邊是 no-op。"""
    src = (REPO / "core/database/schema.py").read_text(encoding="utf-8")
    for table, column in (
        ("user_llm_preferences", "updated_at"),
        ("user_analysis_preferences", "updated_at"),
        ("price_alerts", "created_at"),
    ):
        block = src.split(f"CREATE TABLE IF NOT EXISTS {table} (", 1)[1].split(
            '\n    """)', 1
        )[0]
        assert re.search(rf"{column}\s+TIMESTAMPTZ", block), f"{table}.{column}"


@pytest.fixture
def conn():
    from core.database.connection import get_connection

    try:
        c = get_connection()
    except Exception as e:  # noqa: BLE001 — 連不到就 skip，不是測試失敗
        pytest.skip(f"PostgreSQL 連不到：{e}")
    try:
        yield c
    finally:
        c.rollback()  # 下面的 DDL 全在交易裡，rollback 就還原
        c.close()


def _column_type(cur, table: str, column: str) -> str:
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
        (table, column),
    )
    return cur.fetchone()[0]


def test_migration_round_trips_every_column_without_shifting_values(conn):
    """模擬舊狀態 → 跑 migration → timestamptz；再跑一次不出錯（冪等）；回滾 SQL 變回舊型別。
    全部在交易裡，最後 rollback。session 時區故意不是 UTC，結果不能受影響。"""
    mod = _load()
    cur = conn.cursor()
    # +08:00 固定偏移（不用 'Asia/Taipei'：沒有時區資料庫的 Postgres 也能跑）
    cur.execute("SET LOCAL TIME ZONE INTERVAL '+08:00' HOUR TO MINUTE")
    cur.execute("SHOW TimeZone")
    assert cur.fetchone()[0] != "UTC"

    for spec in mod.CONVERSIONS:
        table, column, old_type = spec[0], spec[1], spec[2]
        # 先降回「舊狀態」（新建的庫是 timestamptz）：這同時驗了回滾 SQL
        cur.execute(mod._convert_sql(spec, to_tz=False))
        assert _column_type(cur, table, column) == old_type

        cur.execute(mod._convert_sql(spec, to_tz=True))
        assert _column_type(cur, table, column) == "timestamp with time zone"
        cur.execute(mod._convert_sql(spec, to_tz=True))  # 冪等
        assert _column_type(cur, table, column) == "timestamp with time zone"

    # 值不偏移：沒時區的 '2026-10-01 01:44:35' 用 UTC 解讀 == 有時區的 01:44:35+00
    cur.execute(
        "SELECT ('2026-10-01 01:44:35.956259'::timestamp AT TIME ZONE 'UTC') "
        "= '2026-10-01 01:44:35.956259+00'::timestamptz"
    )
    assert cur.fetchone()[0] is True


def test_migration_is_a_noop_when_the_table_does_not_exist(conn):
    mod = _load()
    cur = conn.cursor()
    spec = (
        "no_such_table_xyz",
        "updated_at",
        "timestamp without time zone",
        "updated_at AT TIME ZONE 'UTC'",
        "updated_at AT TIME ZONE 'UTC'",
        "TIMESTAMP",
    )
    cur.execute(mod._convert_sql(spec, to_tz=True))
