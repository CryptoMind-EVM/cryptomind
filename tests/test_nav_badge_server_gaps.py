"""側欄未讀數字的伺服器端缺口（#1058 之後）。

1. 私訊未讀要跟列表看得到的對話一致：「刪除對話」「封鎖」後列表看不到，數字卻還算——
   使用者看不到、清不掉；對方再傳訊息時舊的還會疊上去。
2. 封鎖時，對方送我的好友邀請被一起刪掉，鈴鐺裡那筆「接受」鈕要清掉（按了只會 400）。
3. 刪除（或打開已刪除／隱藏）的文章：該篇的論壇通知要清掉，不然論壇紅點永遠對不上文章。
4. 好友邀請被撤回／處理掉而通知早已讀：沒有通知可推，另推 badges_changed 讓側欄與邀請列表重抓。
5. 前端（social-groups／notification-service／nav-badges／獨立頁）在 tests/js 實跑，這裡包成 pytest。

DB 的題目用真 PostgreSQL（同 tests/test_chat_pins.py、test_forum_notifications.py）。
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）


# ── 1. 私訊未讀 vs 列表可見性 ─────────────────────────────


async def _hide_all(s, conv_id: int, user_id: str) -> None:
    """同 hide_conversation_for_user 的刪除記錄部分：對話每則訊息都記一筆 dm_message_deletions"""
    from sqlalchemy import text

    await s.execute(
        text(
            "INSERT INTO dm_message_deletions (message_id, user_id) "
            "SELECT id, :u FROM dm_messages WHERE conversation_id = :c"
        ),
        {"u": user_id, "c": conv_id},
    )


async def test_unread_count_skips_conversations_the_list_hides(gc_pg):
    from core.orm.messages_repo import messages_repo

    s, u, make = gc_pg
    from_a = await messages_repo.send_message(u["a"], u["b"], "hi", session=s)
    await messages_repo.send_message(u["a"], u["b"], "hi 2", session=s)
    await messages_repo.send_message(u["c"], u["b"], "yo", session=s)
    await messages_repo.send_message(u["d"], u["b"], "hey", session=s)
    assert await messages_repo.get_unread_count(u["b"], session=s) == 4

    await _hide_all(s, from_a["message"]["conversation_id"], u["b"])
    assert await messages_repo.get_unread_count(u["b"], session=s) == 2, (
        "b 刪了跟 a 的對話：列表看不到，數字也不能算（使用者清不掉）"
    )

    await make.block(u["b"], u["c"])
    assert await messages_repo.get_unread_count(u["b"], session=s) == 1, (
        "b 封鎖了 c：列表看不到，數字也不能算"
    )
    convs = await messages_repo.get_conversations(u["b"], session=s)
    assert sum(c["unread_count"] for c in convs) == 1, "列表與數字同一個口徑"


async def test_deleted_conversation_reappears_with_new_message_counted(gc_pg):
    """刪除對話之後對方再傳訊息 → 對話重新出現，數字與列表同一個口徑（含上線前刪掉、未讀沒歸零的舊資料）"""
    from core.orm.messages_repo import messages_repo

    s, u, _ = gc_pg
    first = await messages_repo.send_message(u["a"], u["b"], "hi", session=s)
    await _hide_all(s, first["message"]["conversation_id"], u["b"])
    assert await messages_repo.get_unread_count(u["b"], session=s) == 0
    await messages_repo.send_message(u["a"], u["b"], "還在嗎", session=s)
    convs = await messages_repo.get_conversations(u["b"], session=s)
    assert [c["id"] for c in convs] == [first["message"]["conversation_id"]]
    assert await messages_repo.get_unread_count(u["b"], session=s) == sum(
        c["unread_count"] for c in convs
    )
    assert convs[0]["unread_count"] >= 1


async def test_hide_conversation_zeroes_my_unread_and_new_message_does_not_stack():
    """「刪除對話」端點真的有 commit：自己的未讀歸零（對方的不動）；之後來新訊息從 1 算起"""
    from core.database.connection import get_connection
    from core.database.messages.helpers import hide_conversation_for_user
    from core.orm.messages_repo import messages_repo

    try:
        get_connection().close()
    except Exception as e:  # noqa: BLE001 — 連不到就 skip，不是測試失敗
        pytest.skip(f"PostgreSQL 連不到：{e}")

    suffix = uuid.uuid4().hex[:8]
    a, b = f"t-hide-a-{suffix}", f"t-hide-b-{suffix}"
    conn = get_connection()
    try:
        c = conn.cursor()
        for uid in (a, b):
            c.execute(
                "INSERT INTO users (user_id, username) VALUES (%s, %s)", (uid, uid)
            )
        conn.commit()
    finally:
        conn.close()

    conv_id = None
    try:
        for text in ("1", "2"):
            res = await messages_repo.send_message(b, a, text)  # a 收 2 則
            conv_id = res["message"]["conversation_id"]
        await messages_repo.send_message(a, b, "3")  # b 收 1 則
        assert await messages_repo.get_unread_count(a) == 2
        assert await messages_repo.get_unread_count(b) == 1

        assert hide_conversation_for_user(conv_id, a)["success"] is True

        assert await messages_repo.get_unread_count(a) == 0, "a 刪了對話：未讀歸零"
        assert await messages_repo.get_unread_count(b) == 1, "只動自己的，對方不受影響"

        await messages_repo.send_message(b, a, "4")
        assert await messages_repo.get_unread_count(a) == 1, (
            "對話重新出現後從 1 算起，不疊上舊的"
        )
        convs = await messages_repo.get_conversations(a)
        assert [c["unread_count"] for c in convs] == [1]
    finally:
        conn = get_connection()
        try:
            c = conn.cursor()
            if conv_id is not None:
                c.execute(
                    "DELETE FROM dm_message_deletions WHERE message_id IN "
                    "(SELECT id FROM dm_messages WHERE conversation_id = %s)",
                    (conv_id,),
                )
                c.execute(
                    "UPDATE dm_conversations SET last_message_id = NULL WHERE id = %s",
                    (conv_id,),
                )
                c.execute(
                    "DELETE FROM dm_messages WHERE conversation_id = %s", (conv_id,)
                )
                c.execute("DELETE FROM dm_conversations WHERE id = %s", (conv_id,))
            c.execute("DELETE FROM users WHERE user_id = ANY(%s)", ([a, b],))
            conn.commit()
        finally:
            conn.close()


# ── 2. 封鎖：對方送我的好友邀請通知一起收掉 ─────────────────


@pytest.fixture
def block_env(monkeypatch):
    from api.routers import friends as mod

    calls: list = []
    prior = {"status": None}  # 封鎖前兩人之間的狀態（測試自己改）

    async def status(user_id, other, session=None):
        return prior["status"]

    async def block(user_id, target, session=None):
        calls.append(("block", user_id, target))
        return {"success": True, "message": "user_blocked"}

    async def resolve(user_id, from_user_id, session=None):
        calls.append(("resolve", user_id, from_user_id))
        return [f"n-{from_user_id}"]

    async def push_read(user_id, ids):
        calls.append(("push_read", user_id, ids))

    monkeypatch.setattr(mod.friends_repo, "get_friendship_status", status)
    monkeypatch.setattr(mod.friends_repo, "block_user", block)
    monkeypatch.setattr(
        mod.notifications_repo, "resolve_friend_request_notifications", resolve
    )
    monkeypatch.setattr(mod, "push_notifications_read", push_read)
    return mod, calls, prior


def _block(mod, target="bob"):
    return mod.block_user_endpoint.__wrapped__(
        request=MagicMock(),
        req=mod.FriendActionRequest(target_user_id=target),
        current_user={"user_id": "me"},
        session=None,
    )


async def test_block_clears_the_blocked_users_pending_request_notification(block_env):
    """bob 送我的邀請（我還沒處理）：封鎖刪掉它，鈴鐺那筆「接受」鈕要收掉（按了只會 400）"""
    mod, calls, prior = block_env
    prior["status"] = {"status": "pending", "is_requester": False}
    await _block(mod)
    assert ("resolve", "me", "bob") in calls
    assert ("push_read", "me", ["n-bob"]) in calls, "我其他分頁的鈴鐺也要同步"
    assert ("resolve", "bob", "me") not in calls, "我沒送過邀請給 bob：不動 bob 那邊"


async def test_block_clears_my_pending_request_on_the_targets_side(block_env):
    """我送 bob 的邀請還在等：封鎖刪掉它，bob 鈴鐺那筆一樣要收"""
    mod, calls, prior = block_env
    prior["status"] = {"status": "pending", "is_requester": True}
    await _block(mod)
    assert ("resolve", "bob", "me") in calls
    assert ("push_read", "bob", ["n-me"]) in calls


async def test_block_stays_quiet_for_the_target_without_a_pending_request(block_env):
    """一般封鎖（好友、陌生人）：不推任何東西給對方——封鎖不該讓對方的畫面即時變化"""
    mod, calls, prior = block_env
    for current in (None, {"status": "accepted", "is_requester": True}):
        calls.clear()
        prior["status"] = current
        await _block(mod)
        assert [c for c in calls if c[1:2] == ("bob",) and c[0] != "block"] == []
        assert ("resolve", "me", "bob") in calls


# ── 3. 刪除／隱藏的文章：論壇通知清掉 ─────────────────────


def _req(method="GET"):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )


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


async def test_resolve_post_notifications_clears_everyone_for_that_post_only(gc_pg):
    from core.orm.notifications_repo import notifications_repo as repo

    s, u, _ = gc_pg
    p1 = await _post(s, u["a"], "第一篇")
    p2 = await _post(s, u["a"], "第二篇")

    async def notify(to, kind, post_id):
        return await repo.notify_post_activity(
            to_user_id=to,
            kind=kind,
            post_id=post_id,
            post_title="t",
            from_user_id=u["f"],
            from_name="小明",
            session=s,
        )

    n_author = await notify(u["a"], "comment", p1)
    n_thread = await notify(u["c"], "thread_reply", p1)
    n_push = await notify(u["a"], "push", p1)
    n_other_post = await notify(u["a"], "comment", p2)
    assert await repo.has_unread_forum_activity(u["a"], session=s) is True

    cleared = await repo.resolve_post_notifications(p1, session=s)
    assert {k: sorted(v) for k, v in cleared.items()} == {
        u["a"]: sorted([n_author["id"], n_push["id"]]),
        u["c"]: [n_thread["id"]],
    }
    assert await repo.has_unread_forum_activity(u["c"], session=s) is False
    # a 還有第二篇的未讀留言：紅點照亮，只清掉被刪的那篇
    assert await repo.has_unread_forum_activity(u["a"], session=s) is True
    by_id = {n["id"]: n for n in await repo.get_notifications(u["a"], session=s)}
    assert by_id[n_other_post["id"]]["is_read"] is False
    assert await repo.resolve_post_notifications(p1, session=s) == {}, (
        "已讀的不重複回傳"
    )


async def test_delete_post_clears_notifications_for_everyone(monkeypatch):
    from api.routers.forum import posts as mod

    resolved, pushed = [], []

    async def fake_delete(post_id, user_id):
        return True

    async def fake_resolve(post_id, session=None):
        resolved.append(post_id)
        return {"reader1": ["n1"], "reader2": ["n2", "n3"]}

    async def fake_push_read(user_id, ids):
        pushed.append((user_id, ids))

    monkeypatch.setattr(mod.forum_repo, "delete_post", fake_delete)
    monkeypatch.setattr(
        mod.notifications_repo, "resolve_post_notifications", fake_resolve
    )
    monkeypatch.setattr(mod, "push_notifications_read", fake_push_read)

    res = await mod.delete_post_by_id.__wrapped__(
        _req("DELETE"), 5, {"user_id": "author"}
    )
    assert res["success"] is True
    assert resolved == [5]
    assert sorted(pushed) == [("reader1", ["n1"]), ("reader2", ["n2", "n3"])], (
        "每個人開著的鈴鐺都要同步變已讀"
    )


async def test_delete_post_still_succeeds_when_clearing_notifications_fails(
    monkeypatch,
):
    from api.routers.forum import posts as mod

    async def fake_delete(post_id, user_id):
        return True

    async def boom(post_id, session=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(mod.forum_repo, "delete_post", fake_delete)
    monkeypatch.setattr(mod.notifications_repo, "resolve_post_notifications", boom)
    res = await mod.delete_post_by_id.__wrapped__(
        _req("DELETE"), 5, {"user_id": "author"}
    )
    assert res == {"success": True, "message": "Post deleted"}


@pytest.mark.parametrize(
    "post, detail",
    [
        (None, "Post not found"),
        ({"id": 5, "user_id": "author", "is_hidden": 1}, "Post has been hidden"),
    ],
)
async def test_opening_missing_or_hidden_post_clears_my_notifications(
    monkeypatch, post, detail
):
    """通知點進去是 404（刪除／被隱藏）：這篇留在鈴鐺的通知順手清掉，不然紅點永遠在"""
    from fastapi import HTTPException

    from api.routers.forum import posts as mod

    calls, pushed = [], []

    async def fake_get_post(post_id, increment_view=False, viewer_user_id=None):
        return post

    async def fake_mark(user_id, post_id):
        calls.append((user_id, post_id))
        return ["n1"]

    async def fake_push_read(user_id, ids):
        pushed.append((user_id, ids))

    monkeypatch.setattr(mod.forum_repo, "get_post_by_id", fake_get_post)
    monkeypatch.setattr(
        mod.notifications_repo, "mark_post_notifications_read", fake_mark
    )
    monkeypatch.setattr(mod, "push_notifications_read", fake_push_read)
    monkeypatch.setattr(mod, "resolve_request_token", lambda request, token: "tok")
    monkeypatch.setattr(mod, "verify_token", lambda tok: {"sub": "reader"})

    with pytest.raises(HTTPException) as exc:
        await mod.get_post_detail(5, _req(), None)
    assert exc.value.status_code == 404 and exc.value.detail == detail
    assert calls == [("reader", 5)]
    assert pushed == [("reader", ["n1"])]

    calls.clear()
    monkeypatch.setattr(mod, "resolve_request_token", lambda request, token: None)
    with pytest.raises(HTTPException):
        await mod.get_post_detail(5, _req(), None)
    assert calls == [], "訪客沒有通知可清"


# ── 4. badges_changed：邀請數字變了但沒有通知可推 ─────────


async def test_push_badges_changed_sends_ws_message(monkeypatch):
    from api.routers import notifications as mod

    sent = []

    async def fake_send(user_id, data):
        sent.append((user_id, data))

    monkeypatch.setattr(mod.notification_manager, "send_to_user", fake_send)
    await mod.push_badges_changed("bob")
    assert sent == [("bob", {"type": "badges_changed"})]


@pytest.fixture
def push_env(monkeypatch):
    """好友動作成功、通知早已讀（resolve 回空）：只剩 badges_changed 能讓對方的畫面更新"""
    from api.routers import friends as mod

    calls: list = []

    async def ok(*args, **kwargs):
        return {"success": True}

    async def resolve(user_id, from_user_id, session=None):
        return []

    async def push_read(user_id, ids):
        calls.append(("push_read", user_id, ids))

    async def push_badges(user_id):
        calls.append(("badges", user_id))

    async def get_by_id(user_id, session=None):
        return {"user_id": user_id}

    async def create(*args, **kwargs):
        return None

    async def status(user_id, other, session=None):
        return {"status": "pending", "is_requester": True}  # 我送 bob 的邀請還在等

    monkeypatch.setattr(mod.friends_repo, "get_friendship_status", status)
    for name in (
        "accept_friend_request",
        "reject_friend_request",
        "cancel_friend_request",
        "block_user",
    ):
        monkeypatch.setattr(mod.friends_repo, name, ok)
    monkeypatch.setattr(
        mod.notifications_repo, "resolve_friend_request_notifications", resolve
    )
    monkeypatch.setattr(mod.notifications_repo, "create_notification", create)
    monkeypatch.setattr(mod.user_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(mod, "push_notifications_read", push_read)
    monkeypatch.setattr(mod, "push_badges_changed", push_badges)
    return mod, calls


def _friend_call(endpoint, mod, target):
    return endpoint.__wrapped__(
        request=MagicMock(),
        req=mod.FriendActionRequest(target_user_id=target),
        current_user={"user_id": "me", "username": "Me"},
        session=None,
    )


async def test_cancel_pushes_badges_changed_to_the_target(push_env):
    mod, calls = push_env
    await _friend_call(mod.cancel_request, mod, "bob")
    assert ("badges", "bob") in calls, (
        "我撤回邀請：bob 的待處理數字少了，通知早已讀也要讓他重抓"
    )


@pytest.mark.parametrize("name", ["accept_request", "reject_request"])
async def test_accept_reject_push_badges_changed_to_my_other_devices(push_env, name):
    mod, calls = push_env
    await _friend_call(getattr(mod, name), mod, "alice")
    assert ("badges", "me") in calls, "我別台裝置的側欄與邀請列表也要跟著少"


async def test_block_pushes_badges_changed_to_me_and_to_a_target_with_my_pending(
    push_env, monkeypatch
):
    mod, calls = push_env
    await _friend_call(mod.block_user_endpoint, mod, "bob")
    assert ("badges", "me") in calls, "我別台裝置的側欄與邀請列表也要跟著變"
    assert ("badges", "bob") in calls, "我送 bob 的邀請被刪了：bob 的待處理數字要少"

    async def friends_now(user_id, other, session=None):
        return {"status": "accepted", "is_requester": True}

    monkeypatch.setattr(mod.friends_repo, "get_friendship_status", friends_now)
    calls.clear()
    await _friend_call(mod.block_user_endpoint, mod, "bob")
    assert ("badges", "me") in calls
    assert ("badges", "bob") not in calls, "好友之間的封鎖不通知對方"


async def test_badges_push_failure_does_not_break_the_friend_action(
    push_env, monkeypatch
):
    mod, calls = push_env

    async def boom(user_id):
        raise RuntimeError("ws down")

    monkeypatch.setattr(mod, "push_badges_changed", boom)
    result = await _friend_call(mod.cancel_request, mod, "bob")
    assert result == {"success": True}


# ── 5. 前端（node 實跑） ─────────────────────────────────


def test_notification_unread_sync_node_gate():
    """鈴鐺未讀數字以伺服器總數為準、badges_changed 的處理（nav_badges.mjs、social_groups.mjs
    的新增案例各自由 test_nav_badges.py、test_group_chat_frontend.py 跑）"""
    import subprocess
    from pathlib import Path

    script, marker = "notification_unread_sync.mjs", "notification_unread_sync: ok"
    repo = Path(__file__).resolve().parents[1]
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(repo / "tests" / "js" / script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(repo),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert marker in proc.stderr


# ── 獨立頁（governance、scam-tracker 詳情／提交）也要載入 nav-badges ──


def test_classic_pages_with_sidebar_load_nav_badges():
    """這幾頁載了 site-sidebar.js（選單有「社群」「論壇」），但沒載 nav-badges.js，徽章整個不顯示。
    classic 頁走相容橋（classic-compat.js，governance 與 scam-tracker 共用）；
    橋載的檔案 prod image 會清掉沒列白名單的 raw js，所以 Dockerfile 也要保留。"""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    bridge = (repo / "web/js/classic-compat.js").read_text(encoding="utf-8")
    assert "import './nav-badges.js'" in bridge
    dockerfile = (repo / "Dockerfile").read_text(encoding="utf-8")
    assert '! -name "nav-badges.js"' in dockerfile
    for page in (
        "web/governance/index.html",
        "web/scam-tracker/submit.html",
        "web/scam-tracker/detail.html",
    ):
        html = (repo / page).read_text(encoding="utf-8")
        assert "site-sidebar.js" in html
        # 橋（classic-compat.js 或 classic-compat-app.js，後者 import 前者）要在頁面上
        assert "classic-compat" in html, page
