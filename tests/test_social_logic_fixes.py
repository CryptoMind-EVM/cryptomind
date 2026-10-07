"""第二輪盤查（2026-09-25）社群批：私訊額度、付費上限、檢舉上限、好友邀請方向、留言分頁、隱藏文章瀏覽數。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class _Conn:
    def __init__(self, count):
        self.count = count

    def cursor(self):
        conn = self

        class Cur:
            def execute(self, *a, **k):
                pass

            def fetchone(self):
                return (conn.count,)

        return Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class TestMessageLimits:
    def _cfg(self, premium):
        return lambda key, default=None: {
            "limit_daily_message_premium": premium,
            "limit_daily_message_free": 20,
        }.get(key, default)

    def test_premium_limit_is_used_when_configured(self):
        from core.database.messages import limits

        with (
            patch.object(limits, "_get_message_config", side_effect=self._cfg(50)),
            patch.object(limits, "get_connection", return_value=_Conn(21)),
        ):
            inc = limits.check_and_increment_message("u", is_premium=True)
            ro = limits.check_message_limit("u", is_premium=True)
        # 以前掉回免費上限 20 → 第 21 則被擋
        assert inc["limit"] == 50 and inc["can_send"] is True
        assert ro["limit"] == 50

    def test_premium_unlimited_when_not_configured(self):
        from core.database.messages import limits

        with patch.object(limits, "_get_message_config", side_effect=self._cfg(None)):
            assert limits.check_and_increment_message("u", True)["limit"] == -1

    def test_free_limit_unchanged(self):
        from core.database.messages import limits

        with (
            patch.object(limits, "_get_message_config", side_effect=self._cfg(50)),
            patch.object(limits, "get_connection", return_value=_Conn(21)),
        ):
            res = limits.check_and_increment_message("u", is_premium=False)
        assert res["limit"] == 20 and res["can_send"] is False

    def test_failed_send_is_not_charged_twice(self):
        src = (REPO / "api/routers/messages.py").read_text(encoding="utf-8")
        assert "run_sync(increment_message_count" not in src


class TestGovernanceDailyLimit:
    @pytest.mark.parametrize("premium, expected", [(True, 10), (False, 5)])
    def test_router_passes_member_limit(self, premium, expected):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.routers import governance as mod

        app = FastAPI()
        app.include_router(mod.router)

        async def fake_user():
            return {"user_id": "u1"}

        app.dependency_overrides[mod.get_current_user] = fake_user
        create = AsyncMock(return_value={"success": True, "report_id": 1})
        with (
            patch.object(
                mod.user_repo,
                "get_membership",
                new=AsyncMock(return_value={"is_premium": premium}),
            ),
            patch.object(mod.governance_repo, "create_report", new=create),
        ):
            TestClient(app).post(
                "/api/governance/reports",
                json={"content_type": "post", "content_id": 1, "report_type": "spam"},
            )
        assert create.await_args.kwargs["daily_limit"] == expected


class _Session:
    """記下 execute 的 statement；第一個 select 回傳 preset。"""

    def __init__(self, first=None):
        self.stmts = []
        self.first = first

    async def execute(self, stmt):
        self.stmts.append(stmt)
        first = self.first
        return SimpleNamespace(
            scalar_one_or_none=lambda: first,
            first=lambda: None,
            fetchone=lambda: None,
            all=lambda: [],
            fetchall=lambda: [],
            scalar=lambda: 0,
        )

    def add(self, obj):
        pass

    async def flush(self):
        pass


class TestFriendRequestDirection:
    @pytest.mark.asyncio
    async def test_resend_over_reverse_rejected_row_flips_direction(self):
        from core.orm.friends_repo import friends_repo

        # B 曾邀請 A、A 拒絕 → 列是 (B→A, rejected)；現在 A 邀請 B
        existing = SimpleNamespace(id=7, status="rejected", user_id="B", friend_id="A")
        s = _Session(first=existing)
        res = await friends_repo.send_friend_request("A", "B", session=s)
        assert res["success"] is True
        # #954 起同一對人的寫入先拿 advisory lock，statement 順序往後挪：找 UPDATE 那一句，不靠位置
        updates = [_sql(st) for st in s.stmts if _sql(st).lstrip().upper().startswith("UPDATE")]
        assert len(updates) == 1, [_sql(st)[:60] for st in s.stmts]
        sql = updates[0]
        assert "WHERE friendships.id = 7" in sql
        assert (
            "user_id='A'" in sql
            and "friend_id='B'" in sql
            and "status='pending'" in sql
        )


class TestForumQueries:
    @pytest.mark.asyncio
    async def test_comments_exclude_push_boo_rows(self):
        from core.orm.forum_repo import forum_repo

        s = _Session()
        await forum_repo.get_comments(1, session=s)
        assert "forum_comments.type = 'comment'" in _sql(s.stmts[0])

    @pytest.mark.asyncio
    async def test_hidden_post_view_not_counted(self):
        from core.orm.forum_repo import forum_repo

        s = _Session()
        await forum_repo.get_post_by_id(1, increment_view=True, session=s)
        assert "posts.is_hidden = 0" in _sql(s.stmts[0])
