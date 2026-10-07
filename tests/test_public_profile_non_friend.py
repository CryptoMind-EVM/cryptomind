"""個人頁：登入者看「非好友」的個人資料不能 404。

2026-09-26 論壇開放前盤查：get_public_user_profile 把「兩人之間的好友紀錄」子查詢用
add_columns 直接併進去，等於 CROSS JOIN——兩人沒有任何好友紀錄時子查詢 0 列，整筆
結果就 0 列 → 404。從論壇點作者名字（多半不是好友）進個人頁一律「User not found」。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.unit


async def _captured_sql(viewer):
    from core.orm.friends_repo import friends_repo

    result = MagicMock()
    result.fetchone.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    await friends_repo.get_public_user_profile(
        "target-user", viewer_user_id=viewer, session=session
    )
    stmt = session.execute.await_args.args[0]
    return str(stmt.compile(dialect=postgresql.dialect()))


async def test_friendship_is_left_joined_for_logged_in_viewer():
    sql = await _captured_sql("viewer-user")
    assert "LEFT OUTER JOIN" in sql, sql


async def test_guest_query_has_no_friendship_join():
    sql = await _captured_sql(None)
    assert "JOIN" not in sql, sql
