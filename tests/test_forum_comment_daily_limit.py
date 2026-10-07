"""論壇每日回覆上限：免費會員每天 20 則一般留言（推噓不算）。

盤查（2026-09-25）：router 改走 ORM 的 forum_repo.add_comment 之後，舊版
core/database/forum.add_comment 裡的上限檢查與 user_daily_comments 計數都沒搬過來
——免費會員實測連留 22 則全部成功，comment-status 一直顯示 0/20。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import Insert

pytestmark = pytest.mark.unit

USER = {"user_id": "evm_0xfree", "username": "free"}
POST = {"id": 7, "user_id": USER["user_id"], "title": "t", "is_hidden": False}


@pytest.fixture(autouse=True)
def reset_limiter():
    from api.middleware.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


def _post_comment(daily, comment_type="comment"):
    import api.routers.forum.comments as comments

    app = FastAPI()
    app.state.limiter = comments.limiter
    app.include_router(comments.router)

    async def fake_user():
        return USER

    app.dependency_overrides[comments.get_current_user] = fake_user

    async def fake_run_sync(fn, *a, **k):
        assert fn is comments.get_daily_comment_count
        return daily

    add = AsyncMock(return_value={"success": True, "comment_id": 1})
    with (
        patch.object(comments, "run_sync", side_effect=fake_run_sync),
        patch.object(
            comments.forum_repo, "get_post_by_id", new=AsyncMock(return_value=POST)
        ),
        patch.object(comments.forum_repo, "add_comment", new=add),
    ):
        resp = TestClient(app).post(
            "/api/forum/posts/7/comments",
            json={"type": comment_type, "content": "hello"},
        )
    return resp, add


def test_free_member_blocked_when_daily_limit_used_up():
    resp, add = _post_comment({"count": 20, "limit": 20, "remaining": 0})
    assert resp.status_code == 429
    assert "20" in resp.json()["detail"]
    add.assert_not_awaited()


def test_comment_allowed_while_quota_remains():
    resp, add = _post_comment({"count": 19, "limit": 20, "remaining": 1})
    assert resp.status_code == 200
    add.assert_awaited_once()


def test_unlimited_member_allowed():
    # Premium 預設沒有上限：limit / remaining 都是 None
    resp, add = _post_comment({"count": 500, "limit": None, "remaining": None})
    assert resp.status_code == 200
    add.assert_awaited_once()


def test_push_and_boo_not_counted_against_limit():
    for kind in ("push", "boo"):
        resp, add = _post_comment({"count": 20, "limit": 20, "remaining": 0}, kind)
        assert resp.status_code == 200, kind
        add.assert_awaited_once()


def _fake_session():
    session = MagicMock()
    no_vote = MagicMock()
    no_vote.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=no_vote)
    session.flush = AsyncMock()
    return session


def _daily_upserts(session):
    return [
        c.args[0]
        for c in session.execute.await_args_list
        if isinstance(c.args[0], Insert)
        and c.args[0].table.name == "user_daily_comments"
    ]


async def test_repo_counts_plain_comments_only():
    from core.orm.forum_repo import forum_repo

    session = _fake_session()
    await forum_repo.add_comment(7, "u1", "comment", "hi", session=session)
    assert len(_daily_upserts(session)) == 1

    for kind in ("push", "boo"):
        session = _fake_session()
        await forum_repo.add_comment(7, "u1", kind, session=session)
        assert _daily_upserts(session) == [], kind
