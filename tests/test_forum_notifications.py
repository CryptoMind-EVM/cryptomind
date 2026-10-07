"""論壇通知（2026-10-02）：推文／留言合併成一筆、你留言過的文章有新留言、打開文章就已讀。

真 PostgreSQL、交易最後 rollback（共用群組聊天的 fixture：只借它的使用者與 session）。
"""

from __future__ import annotations

import pytest

from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg


async def _post(s, author: str, title: str = "BTC 週線怎麼看") -> int:
    from sqlalchemy import text

    board = (
        await s.execute(
            text(
                "INSERT INTO boards (name, slug) VALUES ('測試板', :slug) RETURNING id"
            ),
            {"slug": f"t-board-{author}-{title}"[:60]},
        )
    ).scalar_one()
    return (
        await s.execute(
            text(
                "INSERT INTO posts (board_id, user_id, category, title, content) "
                "VALUES (:b, :u, 'analysis', :t, '內文') RETURNING id"
            ),
            {"b": board, "u": author, "t": title},
        )
    ).scalar_one()


async def _comment(s, post_id: int, user_id: str, kind: str = "comment", hidden=0):
    from sqlalchemy import text

    await s.execute(
        text(
            "INSERT INTO forum_comments (post_id, user_id, type, content, is_hidden) "
            "VALUES (:p, :u, :k, '內容', :h)"
        ),
        {"p": post_id, "u": user_id, "k": kind, "h": hidden},
    )


def _notify(**kw):
    from core.orm.notifications_repo import notifications_repo

    return notifications_repo.notify_post_activity(**kw)


async def test_push_merges_by_distinct_people(gc_pg):
    s, u, _ = gc_pg
    pid = await _post(s, u["a"])
    base = dict(
        to_user_id=u["a"],
        kind="push",
        post_id=pid,
        post_title="BTC 週線怎麼看",
        session=s,
    )

    first = await _notify(**base, from_user_id=u["b"], from_name="小明")
    assert first["type"] == "post_interaction"
    assert first["data"]["interaction_type"] == "push" and first["data"]["count"] == 1
    second = await _notify(**base, from_user_id=u["c"], from_name="小華")
    again = await _notify(**base, from_user_id=u["b"], from_name="小明")
    assert second["id"] == first["id"] == again["id"], "同一篇還沒讀的推文合併成一筆"
    assert again["data"]["count"] == 2, "同一個人取消再推不重複算"
    assert again["data"]["from_username"] == "小明", "顯示最近一個人"
    assert "小明" in again["body"] and "2" in again["body"]


async def test_comment_push_and_thread_are_separate_and_reset_after_read(gc_pg):
    from core.orm.notifications_repo import notifications_repo

    s, u, _ = gc_pg
    pid = await _post(s, u["a"])
    common = dict(post_id=pid, post_title="BTC 週線怎麼看", session=s)
    push = await _notify(
        to_user_id=u["a"], kind="push", from_user_id=u["b"], from_name="小明", **common
    )
    comment = await _notify(
        to_user_id=u["a"],
        kind="comment",
        from_user_id=u["b"],
        from_name="小明",
        **common,
    )
    thread = await _notify(
        to_user_id=u["c"],
        kind="thread_reply",
        from_user_id=u["b"],
        from_name="小明",
        **common,
    )
    assert len({push["id"], comment["id"], thread["id"]}) == 3
    assert thread["data"]["interaction_type"] == "thread_reply"

    cleared = await notifications_repo.mark_post_notifications_read(
        u["a"], pid, session=s
    )
    assert sorted(cleared) == sorted([push["id"], comment["id"]])
    fresh = await _notify(
        to_user_id=u["a"],
        kind="comment",
        from_user_id=u["d"],
        from_name="阿土",
        **common,
    )
    assert fresh["id"] != comment["id"] and fresh["data"]["count"] == 1, (
        "讀過之後是新的一筆"
    )


async def test_mark_post_read_is_scoped_to_user_and_post(gc_pg):
    from core.orm.notifications_repo import notifications_repo

    s, u, _ = gc_pg
    p1 = await _post(s, u["a"], "第一篇")
    p2 = await _post(s, u["a"], "第二篇")
    n1 = await _notify(
        to_user_id=u["a"],
        kind="comment",
        post_id=p1,
        post_title="第一篇",
        from_user_id=u["b"],
        from_name="小明",
        session=s,
    )
    n2 = await _notify(
        to_user_id=u["a"],
        kind="comment",
        post_id=p2,
        post_title="第二篇",
        from_user_id=u["b"],
        from_name="小明",
        session=s,
    )
    other = await _notify(
        to_user_id=u["c"],
        kind="thread_reply",
        post_id=p1,
        post_title="第一篇",
        from_user_id=u["b"],
        from_name="小明",
        session=s,
    )
    assert await notifications_repo.mark_post_notifications_read(
        u["a"], p1, session=s
    ) == [n1["id"]]
    assert n2["id"] not in await notifications_repo.mark_post_notifications_read(
        u["a"], p1, session=s
    )
    assert await notifications_repo.mark_post_notifications_read(
        u["c"], p1, session=s
    ) == [other["id"]]


async def test_recent_commenter_ids(gc_pg):
    from core.orm.forum_repo import forum_repo

    s, u, _ = gc_pg
    pid = await _post(s, u["a"])
    await _comment(s, pid, u["b"])
    await _comment(s, pid, u["c"])
    await _comment(s, pid, u["b"])  # 同一人兩則
    await _comment(s, pid, u["d"], kind="push")  # 推文不算留言
    await _comment(s, pid, u["e"], hidden=1)  # 被隱藏的不算
    await _comment(s, pid, u["a"])  # 作者自己

    ids = await forum_repo.recent_commenter_ids(
        pid, exclude={u["a"], u["c"]}, session=s
    )
    assert ids == [u["b"]]
    assert sorted(
        await forum_repo.recent_commenter_ids(pid, exclude=set(), session=s)
    ) == sorted([u["a"], u["b"], u["c"]])
    assert (
        len(
            await forum_repo.recent_commenter_ids(
                pid, exclude=set(), limit=1, session=s
            )
        )
        == 1
    )


def test_describe_node_gate():
    """鈴鐺文字：推／留言／也在這篇留言走 i18n、一人與多人不同句、舊列照原文"""
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["node", str(repo / "tests" / "js" / "forum_notifications.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=repo,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "forum_notifications: ok" in proc.stderr


async def test_has_unread_forum_activity_ignores_pushes(gc_pg):
    """論壇紅點：留言類才亮，推不亮；讀了就熄"""
    from core.orm.notifications_repo import notifications_repo

    s, u, _ = gc_pg
    pid = await _post(s, u["a"])
    common = dict(
        post_id=pid,
        post_title="BTC 週線怎麼看",
        from_user_id=u["b"],
        from_name="小明",
        session=s,
    )
    has = notifications_repo.has_unread_forum_activity
    assert await has(u["a"], session=s) is False
    await _notify(to_user_id=u["a"], kind="push", **common)
    assert await has(u["a"], session=s) is False, "推不點亮"
    await _notify(to_user_id=u["a"], kind="comment", **common)
    assert await has(u["a"], session=s) is True
    await _notify(to_user_id=u["c"], kind="thread_reply", **common)
    assert await has(u["c"], session=s) is True
    await notifications_repo.mark_post_notifications_read(u["a"], pid, session=s)
    assert await has(u["a"], session=s) is False
