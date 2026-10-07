"""論壇通知怎麼發（api/routers/forum/comments.py、posts.py）：repo 換成假的，直接呼叫 endpoint。

- 推：只有「推下去」才發（取消推不發）；噓不發；推自己的文章不發
- 留言：通知作者（comment）＋在這篇留過言的其他人（thread_reply），自己與作者不重複
- 打開文章：這篇的論壇通知變已讀，推給其他分頁
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

pytestmark = pytest.mark.unit

POST = {"id": 5, "user_id": "author", "title": "BTC 週線怎麼看", "is_hidden": 0}


def _req(method="POST"):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )


@pytest.fixture
def comments(monkeypatch):
    from api.routers.forum import comments as mod

    sent, pushed = [], []

    async def fake_get_post(post_id, increment_view=False, viewer_user_id=None):
        return dict(POST)

    async def fake_notify(**kw):
        sent.append((kw["to_user_id"], kw["kind"]))
        return {"id": f"n-{kw['to_user_id']}-{kw['kind']}"}

    async def fake_push(user_id, notification):
        pushed.append(user_id)

    async def allow(title, content):
        return {"status": "pass", "flagged": False}

    async def commenters(post_id, exclude, limit=100, session=None):
        return [u for u in ["old1", "old2", "author", "actor"] if u not in exclude]

    monkeypatch.setattr(mod.forum_repo, "get_post_by_id", fake_get_post)
    monkeypatch.setattr(mod.forum_repo, "recent_commenter_ids", commenters)
    monkeypatch.setattr(mod.notifications_repo, "notify_post_activity", fake_notify)
    monkeypatch.setattr(mod, "push_notification_to_user", fake_push)
    monkeypatch.setattr(mod, "check_post", allow)
    monkeypatch.setattr(mod, "record_moderation", lambda *a, **kw: None)
    monkeypatch.setattr(
        mod, "get_daily_comment_count", lambda uid: {"remaining": 5, "limit": 10}
    )
    mod._test_sent, mod._test_pushed = sent, pushed
    return mod


def _vote(monkeypatch, mod, action):
    async def fake_add(**kw):
        return {"success": True, "action": action, "comment_id": 1}

    monkeypatch.setattr(mod.forum_repo, "add_comment", fake_add)


async def test_push_notifies_only_when_voted(monkeypatch, comments):
    user = {"user_id": "actor", "username": "小明"}
    _vote(monkeypatch, comments, "voted")
    await comments._react_post(5, "push", "actor", None, user)
    assert comments._test_sent == [("author", "push")]
    assert comments._test_pushed == ["author"]

    comments._test_sent.clear()
    _vote(monkeypatch, comments, "cancelled")
    await comments._react_post(5, "push", "actor", None, user)
    assert comments._test_sent == [], "取消推不發（以前會再發一次「互動了」）"


async def test_boo_and_self_push_do_not_notify(monkeypatch, comments):
    _vote(monkeypatch, comments, "voted")
    await comments._react_post(5, "boo", "actor", None, {"user_id": "actor"})
    await comments._react_post(5, "push", "author", None, {"user_id": "author"})
    assert comments._test_sent == []


async def test_comment_notifies_author_and_thread(monkeypatch, comments):
    async def fake_add(**kw):
        return {"success": True, "comment_id": 9}

    monkeypatch.setattr(comments.forum_repo, "add_comment", fake_add)
    body = comments.AddCommentRequest(type="comment", content="同意")
    await comments.add_new_comment.__wrapped__(
        _req(), 5, body, {"user_id": "actor", "username": "小明"}
    )
    assert comments._test_sent == [
        ("author", "comment"),
        ("old1", "thread_reply"),
        ("old2", "thread_reply"),
    ], "作者收「留言了你的文章」，其他留過言的收「也在這篇留言」，不含自己"

    comments._test_sent.clear()
    await comments.add_new_comment.__wrapped__(
        _req(), 5, body, {"user_id": "author", "username": "作者"}
    )
    assert comments._test_sent == [
        ("old1", "thread_reply"),
        ("old2", "thread_reply"),
        ("actor", "thread_reply"),
    ], "作者自己留言：只通知其他留過言的人"


async def test_comment_endpoint_push_type_uses_vote_rules(monkeypatch, comments):
    _vote(monkeypatch, comments, "voted")
    body = comments.AddCommentRequest(type="boo", content="不同意")
    await comments.add_new_comment.__wrapped__(_req(), 5, body, {"user_id": "actor"})
    assert comments._test_sent == [], "留言 API 帶 boo 也照噓的規則：不發"


async def test_opening_post_marks_its_notifications_read(monkeypatch):
    from api.routers.forum import posts as mod

    calls, pushed_read = [], []

    async def fake_get_post(post_id, increment_view=False, viewer_user_id=None):
        return dict(POST)

    async def fake_mark(user_id, post_id):
        calls.append((user_id, post_id))
        return ["n1", "n2"]

    async def fake_push_read(user_id, ids):
        pushed_read.append((user_id, ids))

    async def no_wallets(uid):
        return []

    monkeypatch.setattr(mod.forum_repo, "get_post_by_id", fake_get_post)
    monkeypatch.setattr(
        mod.notifications_repo, "mark_post_notifications_read", fake_mark
    )
    monkeypatch.setattr(mod, "push_notifications_read", fake_push_read)
    monkeypatch.setattr(mod, "author_evm_addresses", no_wallets)
    monkeypatch.setattr(mod, "resolve_request_token", lambda request, token: "tok")
    monkeypatch.setattr(mod, "verify_token", lambda tok: {"sub": "reader"})

    await mod.get_post_detail(5, _req("GET"), None)
    assert calls == [("reader", 5)]
    assert pushed_read == [("reader", ["n1", "n2"])]

    calls.clear()
    monkeypatch.setattr(mod, "resolve_request_token", lambda request, token: None)
    await mod.get_post_detail(5, _req("GET"), None)
    assert calls == [], "訪客沒有通知可清"
