"""私訊「載入更舊訊息」（2026-09-25 盤查）。

前端：從「跟某人聊」入口開的對話從沒設分頁狀態，捲上去載不了舊訊息；先前選過別的對話時還拿那個對話的
游標去抓（訊息 id 是全站流水號 → 抓回已顯示的訊息，重複）。原本出事的是獨立私訊頁 messages_page.js
（2026-10-05 已併入好友頁 SocialHub，同樣的行為由 tests/js/frontend_feature_bugs.mjs 的 2d 段
守，node 實跑）。

後端：往前翻頁的 /api/messages/conversation/{id} 走 ORM messages_repo.get_messages，
沒排除自己「刪除（只對自己隱藏）」的訊息——第一頁（/with/，舊 psycopg2 路徑）
有排除，捲上去就冒出來。
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from core.orm.messages_repo import messages_repo

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, scalar=None, rows=()):
        self._scalar = scalar
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._rows


class _Session:
    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        # 第一次是參與者驗證，第二次是訊息查詢
        return _Result(scalar=7) if len(self.statements) == 1 else _Result(rows=[])


def _sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


async def test_older_page_excludes_messages_the_viewer_hid():
    session = _Session()

    result = await messages_repo.get_messages(
        7, "viewer-1", limit=20, before_id=100, session=session
    )

    assert result == {"success": True, "messages": [], "has_more": False}
    sql = _sql(session.statements[1])
    assert "dm_message_deletions" in sql, (
        "翻頁查詢沒排除自己隱藏的訊息：第一頁看不到，捲上去又冒出來"
    )
    assert "dm_message_deletions.user_id = 'viewer-1'" in sql, sql
    assert "dm_message_deletions.id IS NULL" in sql, sql
    assert "dm_messages.id < 100" in sql, sql
