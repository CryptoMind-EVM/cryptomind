"""群組聊天 API（api/routers/group_chat.py）：開關、Pro 檢查、推播扇出、通知。

repo 換成假的（repo 本身在 test_group_chat_repo／test_group_messages_repo 用真 PG 測），
直接呼叫 endpoint.__wrapped__（跳過 limiter），同 test_dm_recall 的做法。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


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
def router(monkeypatch):
    from api.routers import group_chat as router

    pushed, notified, read_ids = [], [], []

    async def fake_send(user_id, payload):
        pushed.append((user_id, payload))

    async def fake_push(user_id, notification):
        notified.append((user_id, notification))

    async def fake_read(user_id, ids):
        read_ids.append((user_id, ids))

    monkeypatch.setattr(router.message_manager, "send_to_user", fake_send)
    monkeypatch.setattr(router, "push_notification_to_user", fake_push)
    monkeypatch.setattr(router, "push_notifications_read", fake_read)
    monkeypatch.setattr(
        router, "get_user_membership", lambda uid: {"is_premium": uid != "free"}
    )

    async def fake_name(uid):
        return uid

    monkeypatch.setattr(router, "_display_name", fake_name)

    async def flag_on(key, default=None, session=None):
        return True  # 開關預設開；要測關閉的測試自己蓋掉

    monkeypatch.setattr(router.config_repo, "get_config", flag_on)
    router._test_pushed, router._test_notified, router._test_read = (
        pushed,
        notified,
        read_ids,
    )
    return router


def _types(pushed, user_id):
    return [p["type"] for uid, p in pushed if uid == user_id]


# ── 守衛 ──────────────────────────────────────────────


def test_limiter_decorator_is_below_router_decorator():
    """@limiter 在 @router 上面時 router 登記的是沒包 limiter 的函式，限流等於沒有（7 條舊路由的教訓）"""
    lines = (
        (REPO / "api/routers/group_chat.py").read_text(encoding="utf-8").splitlines()
    )
    limiter_lines = [
        i for i, line in enumerate(lines) if line.startswith("@limiter.limit")
    ]
    assert limiter_lines, "每條路由都要有 rate limit"
    for i in limiter_lines:
        assert lines[i - 1].startswith("@router."), (
            f"第 {i + 1} 行：@limiter 要緊接在 @router 下面"
        )
    routes = [
        line
        for line in lines
        if re.match(r"@router\.(get|post|put|patch|delete)\(", line)
    ]
    assert len(routes) == len(limiter_lines), "每條路由都要有 rate limit"


async def test_disabled_flag_is_404(monkeypatch, router):
    async def off(key, default=None, session=None):
        return False

    monkeypatch.setattr(router.config_repo, "get_config", off)
    with pytest.raises(HTTPException) as exc:
        await router.require_group_chat_enabled()
    assert exc.value.status_code == 404

    async def on(key, default=None, session=None):
        return True

    monkeypatch.setattr(router.config_repo, "get_config", on)
    await router.require_group_chat_enabled()


def test_router_registered_with_flag_dependency():
    src = (REPO / "api_server.py").read_text(encoding="utf-8")
    assert "group_chat_router" in src
    router_src = (REPO / "api/routers/group_chat.py").read_text(encoding="utf-8")
    assert "dependencies=[Depends(require_group_chat_enabled)]" in router_src


# ── 送訊息 ────────────────────────────────────────────


async def test_send_fans_out_and_notifies_unmuted(monkeypatch, router):
    msg = {
        "id": 7,
        "group_id": 3,
        "from_user_id": "a",
        "from_display_name": "小明",
        "content": "早安",
    }

    async def fake_send_message(group_id, user_id, content, reply_to_message_id=None):
        return {
            "success": True,
            "message": msg,
            "member_ids": ["a", "b", "c"],
            "group_name": "投資閒聊",
        }

    async def fake_unmuted(group_id):
        return ["a", "b"]  # c 關了通知

    calls = []

    async def fake_notify(**kw):
        calls.append(kw)
        return {"id": f"n-{kw['to_user_id']}", "type": "group_message"}

    monkeypatch.setattr(router.group_messages_repo, "send_message", fake_send_message)
    monkeypatch.setattr(router.group_chat_repo, "unmuted_member_ids", fake_unmuted)
    monkeypatch.setattr(router.notifications_repo, "notify_group_message", fake_notify)

    body = router.SendGroupMessageRequest(content="早安")
    result = await router.send_group_message.__wrapped__(
        _req(), 3, body, {"user_id": "a"}
    )

    assert result["message"]["id"] == 7
    for uid in ("a", "b", "c"):
        assert _types(router._test_pushed, uid) == ["group_message"], (
            "全部成員（含自己其他裝置）都推"
        )
    assert [c["to_user_id"] for c in calls] == ["b"], "自己不通知、關通知的不通知"
    assert router._test_notified == [("b", {"id": "n-b", "type": "group_message"})]


async def test_send_notifies_mentioned_even_if_muted(monkeypatch, router):
    """被 @ 的人：關了通知也發、標 mentioned；而且在推訊息之前發（列表重抓時就看得到「有人提及你」）"""
    events = []
    msg = {"id": 8, "group_id": 3, "from_user_id": "a", "content": "@B @C 看"}

    async def fake_send_message(group_id, user_id, content, reply_to_message_id=None):
        return {
            "success": True,
            "message": msg,
            "member_ids": ["a", "b", "c", "d"],
            "group_name": "投資閒聊",
            "mentioned_ids": ["b", "c"],
        }

    async def fake_unmuted(group_id):
        return ["a", "b", "d"]  # c 關了通知

    async def fake_notify(**kw):
        events.append(("notify", kw["to_user_id"], kw.get("mentioned", False)))
        return {"id": f"n-{kw['to_user_id']}"}

    async def fake_send(user_id, payload):
        events.append(("push", user_id, payload["type"]))

    monkeypatch.setattr(router.group_messages_repo, "send_message", fake_send_message)
    monkeypatch.setattr(router.group_chat_repo, "unmuted_member_ids", fake_unmuted)
    monkeypatch.setattr(router.notifications_repo, "notify_group_message", fake_notify)
    monkeypatch.setattr(router.message_manager, "send_to_user", fake_send)

    await router.send_group_message.__wrapped__(
        _req(), 3, router.SendGroupMessageRequest(content="@B @C 看"), {"user_id": "a"}
    )
    notifies = [e for e in events if e[0] == "notify"]
    assert sorted(notifies) == [
        ("notify", "b", True),
        ("notify", "c", True),
        ("notify", "d", False),
    ], "b 只發一次（標 mentioned）、c 關了通知照發、自己不發"
    first_push = next(i for i, e in enumerate(events) if e[0] == "push")
    mention_idx = [i for i, e in enumerate(events) if e[0] == "notify" and e[2]]
    assert max(mention_idx) < first_push, "提及通知要在推訊息之前"


async def test_list_groups_marks_unread_mentions(monkeypatch, router):
    async def fake_list(user_id):
        return [
            {"id": 1, "unread_count": 2},
            {"id": 2, "unread_count": 0},  # 通知沒清到但其實讀過了：不標
            {"id": 3, "unread_count": 5},
        ]

    async def fake_mentions(user_id):
        assert user_id == "a"
        return {1, 2}

    monkeypatch.setattr(router.group_chat_repo, "list_groups", fake_list)
    monkeypatch.setattr(
        router.notifications_repo, "unread_mention_group_ids", fake_mentions
    )
    result = await router.list_groups.__wrapped__(_req("GET"), {"user_id": "a"})
    assert [g["mentioned"] for g in result["groups"]] == [True, False, False]


async def test_send_requires_pro_and_membership(monkeypatch, router):
    body = router.SendGroupMessageRequest(content="hi")
    with pytest.raises(HTTPException) as exc:
        await router.send_group_message.__wrapped__(
            _req(), 3, body, {"user_id": "free"}
        )
    assert exc.value.status_code == 403 and exc.value.detail == "pro_required"

    async def not_member(*a, **kw):
        return {"success": False, "error": "not_found"}

    monkeypatch.setattr(router.group_messages_repo, "send_message", not_member)
    with pytest.raises(HTTPException) as exc:
        await router.send_group_message.__wrapped__(_req(), 3, body, {"user_id": "a"})
    assert exc.value.status_code == 404


async def test_get_group_non_member_is_404(monkeypatch, router):
    async def none(group_id, user_id):
        return None

    monkeypatch.setattr(router.group_chat_repo, "get_group", none)
    with pytest.raises(HTTPException) as exc:
        await router.get_group.__wrapped__(_req("GET"), 3, {"user_id": "x"})
    assert exc.value.status_code == 404


# ── 邀請 ──────────────────────────────────────────────


async def test_invite_creates_notifications(monkeypatch, router):
    async def fake_invites(group_id, inviter_id, invitee_ids):
        return {
            "success": True,
            "group_name": "投資閒聊",
            "invited": [{"invite_id": 11, "invitee_id": "b"}],
            "skipped": [{"user_id": "c", "reason": "not_friend"}],
        }

    created = []

    async def fake_create(
        user_id, notification_type, title, body, data=None, session=None
    ):
        created.append((user_id, notification_type, data))
        return {"id": "n1", "type": notification_type, "data": data}

    monkeypatch.setattr(router.group_chat_repo, "create_invites", fake_invites)
    monkeypatch.setattr(router.notifications_repo, "create_notification", fake_create)

    body = router.InviteRequest(user_ids=["b", "c"])
    result = await router.invite_members.__wrapped__(_req(), 3, body, {"user_id": "a"})
    assert result["invited"] == [{"invite_id": 11, "invitee_id": "b"}]
    assert result["skipped"] == [{"user_id": "c", "reason": "not_friend"}]
    (user_id, ntype, data) = created[0]
    assert user_id == "b" and ntype == "group_invite"
    assert (
        data["invite_id"] == 11
        and data["group_id"] == 3
        and data["group_name"] == "投資閒聊"
    )
    assert router._test_notified[0][0] == "b"


async def test_accept_clears_invite_notification_and_broadcasts(monkeypatch, router):
    system = {
        "id": 20,
        "group_id": 3,
        "content": "member_joined:b",
        "message_type": "system",
    }

    async def fake_accept(invite_id, user_id):
        return {
            "success": True,
            "group_id": 3,
            "group_name": "投資閒聊",
            "inviter_id": "a",
            "system_message": system,
            "member_ids": ["a", "b"],
        }

    async def fake_resolve(user_id, invite_id):
        return ["n-invite"]

    monkeypatch.setattr(router.group_chat_repo, "accept_invite", fake_accept)
    monkeypatch.setattr(
        router.notifications_repo, "resolve_group_invite_notifications", fake_resolve
    )

    result = await router.accept_invite.__wrapped__(_req(), 11, {"user_id": "b"})
    assert result == {"success": True, "group_id": 3}
    assert router._test_read == [("b", ["n-invite"])]
    for uid in ("a", "b"):
        assert _types(router._test_pushed, uid) == ["group_message", "group_updated"]


async def test_accept_errors(monkeypatch, router):
    async def full(invite_id, user_id):
        return {"success": False, "error": "group_full"}

    monkeypatch.setattr(router.group_chat_repo, "accept_invite", full)
    with pytest.raises(HTTPException) as exc:
        await router.accept_invite.__wrapped__(_req(), 11, {"user_id": "b"})
    assert exc.value.status_code == 409 and exc.value.detail == "group_full"
    with pytest.raises(HTTPException) as exc:
        await router.accept_invite.__wrapped__(_req(), 11, {"user_id": "free"})
    assert exc.value.detail == "pro_required"


# ── 踢人／已讀 ────────────────────────────────────────


async def test_removed_member_gets_notified(monkeypatch, router):
    async def fake_remove(group_id, owner_id, target_id):
        return {
            "success": True,
            "group_name": "投資閒聊",
            "system_message": {"id": 30, "message_type": "system"},
            "member_ids": ["a", "b"],
        }

    created = []

    async def fake_create(
        user_id, notification_type, title, body, data=None, session=None
    ):
        created.append((user_id, notification_type))
        return {"id": "n2", "type": notification_type}

    monkeypatch.setattr(router.group_chat_repo, "remove_member", fake_remove)
    monkeypatch.setattr(router.notifications_repo, "create_notification", fake_create)

    await router.remove_member.__wrapped__(_req("DELETE"), 3, "c", {"user_id": "a"})
    assert created == [("c", "group_removed")]
    assert [p["kind"] for uid, p in router._test_pushed if uid == "c"] == ["removed"]
    assert _types(router._test_pushed, "a") == ["group_message", "group_updated"]


async def test_transfer_owner_broadcasts_and_maps_errors(monkeypatch, router):
    calls = []

    async def fake_transfer(group_id, owner_id, target_id):
        calls.append((group_id, owner_id, target_id))
        if target_id == "free":
            return {"success": False, "error": "target_not_premium"}
        return {
            "success": True,
            "system_message": {"id": 31, "message_type": "system"},
            "member_ids": ["a", "b"],
        }

    monkeypatch.setattr(router.group_chat_repo, "transfer_owner", fake_transfer)

    body = router.TransferOwnerRequest(user_id="b")
    result = await router.transfer_owner.__wrapped__(_req(), 3, body, {"user_id": "a"})
    assert result == {"success": True, "owner_id": "b"}
    assert calls == [(3, "a", "b")]
    for uid in ("a", "b"):
        assert _types(router._test_pushed, uid) == ["group_message", "group_updated"]
    assert [p.get("kind") for uid, p in router._test_pushed if uid == "b"][-1] == (
        "owner_changed"
    )

    with pytest.raises(HTTPException) as exc:
        await router.transfer_owner.__wrapped__(
            _req(), 3, router.TransferOwnerRequest(user_id="free"), {"user_id": "a"}
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "target_not_premium"

    with pytest.raises(HTTPException) as exc:
        await router.transfer_owner.__wrapped__(_req(), 3, body, {"user_id": "free"})
    assert exc.value.detail == "pro_required", "群主自己 Pro 到期也不能管理"


async def test_read_pushes_only_when_changed(monkeypatch, router):
    state = {"changed": True}

    async def fake_mark(group_id, user_id, last_message_id):
        return {
            "success": True,
            "changed": state["changed"],
            "last_read_message_id": 9,
            "member_ids": ["a", "b"],
        }

    async def fake_clear(user_id, group_id):
        return []

    monkeypatch.setattr(router.group_messages_repo, "mark_read", fake_mark)
    monkeypatch.setattr(
        router.notifications_repo, "mark_group_message_notifications_read", fake_clear
    )

    body = router.MarkGroupReadRequest(last_message_id=9)
    await router.mark_group_read.__wrapped__(_req(), 3, body, {"user_id": "b"})
    assert [(uid, p["type"], p["user_id"]) for uid, p in router._test_pushed] == [
        ("a", "group_read", "b"),
        ("b", "group_read", "b"),
    ]
    router._test_pushed.clear()
    state["changed"] = False
    await router.mark_group_read.__wrapped__(_req(), 3, body, {"user_id": "b"})
    assert router._test_pushed == []


# ── 輸入中 ────────────────────────────────────────────


async def test_group_typing_relays_to_other_members_with_throttle(monkeypatch, router):
    calls = {"n": 0}

    async def fake_members(group_id):
        calls["n"] += 1
        return ["a", "b", "c"] if group_id == 3 else []

    monkeypatch.setattr(router.group_chat_repo, "member_ids", fake_members)
    clock = {"t": 100.0}
    state: dict = {}
    now = lambda: clock["t"]  # noqa: E731

    await router.relay_group_typing(
        "a", "小明", {"group_id": 3, "state": "start"}, state, now=now
    )
    assert sorted(uid for uid, _ in router._test_pushed) == ["b", "c"], "不轉給自己"
    assert router._test_pushed[0][1]["type"] == "group_typing"

    router._test_pushed.clear()
    clock["t"] += 0.5
    await router.relay_group_typing(
        "a", "小明", {"group_id": 3, "state": "start"}, state, now=now
    )
    assert router._test_pushed == [], "1 秒內同群不重送"

    clock["t"] += 2
    await router.relay_group_typing(
        "a", "小明", {"group_id": 3, "state": "start"}, state, now=now
    )
    assert calls["n"] == 1, "成員資格快取 60 秒"

    router._test_pushed.clear()
    await router.relay_group_typing(
        "x", "陌生人", {"group_id": 3, "state": "start"}, {}, now=now
    )
    assert router._test_pushed == [], "非成員不轉送"
    await router.relay_group_typing(
        "a", "小明", {"group_id": "3", "state": "start"}, {}, now=now
    )
    assert router._test_pushed == [], "group_id 要是整數"


# ── review 修正（2026-10-01）────────────────────────────


def test_name_limit_matches_repo_and_db():
    """API 合約要跟 repo／DB 一致（30 字），不然前端照 OpenAPI 送 45 字會莫名 400"""
    from pydantic import ValidationError

    from api.routers.group_chat import CreateGroupRequest, UpdateGroupRequest
    from core.orm.group_chat_repo import NAME_MAX

    assert NAME_MAX == 30
    CreateGroupRequest(name="x" * 30)
    for model in (CreateGroupRequest, UpdateGroupRequest):
        with pytest.raises(ValidationError):
            model(name="x" * 31)


async def test_group_typing_respects_feature_flag(monkeypatch, router):
    async def off(key, default=None, session=None):
        return False

    async def members(group_id):
        return ["a", "b"]

    monkeypatch.setattr(router.config_repo, "get_config", off)
    monkeypatch.setattr(router.group_chat_repo, "member_ids", members)
    await router.relay_group_typing(
        "a", "小明", {"group_id": 3, "state": "start"}, {}, now=lambda: 1.0
    )
    assert router._test_pushed == [], "開關關著不轉送群組 typing"


async def test_deleting_group_clears_pending_invite_notifications(monkeypatch, router):
    async def fake_leave(group_id, user_id):
        return {
            "success": True,
            "deleted": True,
            "new_owner_id": None,
            "system_messages": [],
            "member_ids": [],
            "cancelled_invites": [{"invite_id": 11, "invitee_id": "b"}],
        }

    async def fake_resolve(user_id, invite_id):
        return [f"n-{user_id}-{invite_id}"]

    monkeypatch.setattr(router.group_chat_repo, "leave_group", fake_leave)
    monkeypatch.setattr(
        router.notifications_repo, "resolve_group_invite_notifications", fake_resolve
    )
    await router.leave_group.__wrapped__(_req(), 3, {"user_id": "a"})
    assert router._test_read == [("b", ["n-b-11"])], (
        "群刪了，被邀的人鈴鐺裡的接受鈕要拿掉"
    )
