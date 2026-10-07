"""init_db() 建出來的時間欄位要跟 ORM 宣告、正式站一致。

背景（2026-10-03）：core/database/schema.py 的建表語法有 43 個欄位是沒時區的 TIMESTAMP，
但正式站與 ORM（TIMESTAMP(timezone=True)）都是 timestamptz。測試庫／全新資料庫只跑 init_db，
就會得到沒時區的欄位，程式一比較時間就噴 "can't compare offset-naive and offset-aware
datetimes"（#1018 當時用測試設定轉型別暫時掩蓋，這裡把根因修在建表語法）。

守門：全新 init_db 之後，ORM 宣告有時區的欄位，資料庫裡也必須是 timestamptz。
連不到資料庫就略過（CI 有 postgres:16）。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def conn():
    from core.database.connection import get_connection

    try:
        c = get_connection()  # 第一次呼叫會跑 init_db
    except Exception as e:  # noqa: BLE001 — 連不到就 skip，不是測試失敗
        pytest.skip(f"PostgreSQL 連不到：{e}")
    try:
        yield c
    finally:
        c.close()


def test_orm_timezone_columns_are_timestamptz_after_init_db(conn):
    """ORM 宣告有時區（TIMESTAMP(timezone=True)）的欄位，init_db 建出來也必須是 timestamptz。
    任何其他型別都算失敗（包含 text：price_alerts.created_at 以前就是 TEXT，2026-10-03 從零重建時才發現）。"""
    from sqlalchemy import DateTime

    from core.orm.models import Base

    cur = conn.cursor()
    cur.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public'"
    )
    actual = {(t, c): ty for t, c, ty in (tuple(r) for r in cur.fetchall())}
    bad = sorted(
        f"{table.name}.{col.name}（{actual[(table.name, col.name)]}）"
        for table in Base.metadata.sorted_tables
        for col in table.columns
        if isinstance(col.type, DateTime)
        and col.type.timezone
        and (table.name, col.name) in actual
        and actual[(table.name, col.name)] != "timestamp with time zone"
    )
    assert not bad, (
        "ORM 宣告有時區（TIMESTAMP(timezone=True)）、但 init_db 建出來不是 timestamptz："
        f"{bad}。把 core/database/schema.py 對應欄位改成 TIMESTAMPTZ，"
        "並確認正式站該欄位的實際型別（可參考 alembic c069）。"
    )
