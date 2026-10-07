"""群主解散群組（c068）：group_chat_repo.dissolve_group＋POST /api/groups/{id}/dissolve。

- repo 用真 PostgreSQL、整段交易最後 rollback（tests/group_chat_pg）。測試庫不跑 alembic：
  pg_conn_factory 讓第一次 get_connection() 跑 init_db（schema.py 補 dissolved_at）。
- 路由把 repo／推播換成假的，直接呼叫 endpoint.__wrapped__（同 test_group_chat_router）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from tests import group_chat_pg
from tests.test_group_chat_router import _req, _types
from tests.test_group_chat_router import router as router  # noqa: F401 — 共用 fixture

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）


@pytest.fixture(scope="module")
def pg_conn_factory():
    from core.database.connection import get_connection

    try:
        get_connection().close()
    except Exception as e:  # noqa: BLE001 — 連不到就 skip，不是測試失敗
        pytest.skip(f"PostgreSQL 連不到：{e}")
    return get_connection


@pytest.fixture
async def dpg(pg_conn_factory, gc_pg):
    return gc_pg


# ── 共用 ──────────────────────────────────────────────


async def _group(s, owner, name="投資閒聊"):
    from core.orm.group_chat_repo import group_chat_repo

    result = await group_chat_repo.create_group(owner, name, session=s)
    assert result["success"] is True, result
    return result["group"]["id"]


async def _join(s, make, gid, inviter, invitee):
    from core.orm.group_chat_repo import group_chat_repo

    await make.friends(inviter, invitee)
    inv = await group_chat_repo.create_invites(gid, inviter, [invitee], session=s)
    assert inv["invited"], inv
    acc = await group_chat_repo.accept_invite(
        inv["invited"][0]["invite_id"], invitee, session=s
    )
    assert acc["success"] is True, acc


async def _invite(s, make, gid, inviter, invitee) -> int:
    from core.orm.group_chat_repo import group_chat_repo

    await make.friends(inviter, invitee)
    inv = await group_chat_repo.create_invites(gid, inviter, [invitee], session=s)
    assert inv["invited"], inv
    return inv["invited"][0]["invite_id"]


async def _row(s, sql, **params):
    from sqlalchemy import text

    return (await s.execute(text(sql), params)).first()


async def _setup(s, u, make):
    """a 開群、b c 入群、d 待處理邀請、b 傳一則訊息"""
    from core.orm.group_messages_repo import group_messages_repo

    gid = await _group(s, u["a"])
    await _join(s, make, gid, u["a"], u["b"])
    await _join(s, make, gid, u["a"], u["c"])
    invite_id = await _invite(s, make, gid, u["a"], u["d"])
    sent = await group_messages_repo.send_message(gid, u["b"], "保證獲利", session=s)
    assert sent["success"] is True, sent
    return gid, invite_id, sent["message"]["id"]


# ── schema 三處同步 ───────────────────────────────────


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_migration_schema_and_models_in_sync():
    src = _read("alembic/versions/c068_chat_order_group_dissolve.py")
    assert re.search(r'^revision = "c068"$', src, re.M)
    assert re.search(r'^down_revision = "c067"$', src, re.M)
    upgrade, downgrade = src.split("def downgrade")
    assert "CREATE TABLE IF NOT EXISTS chat_order" in upgrade
    assert "idx_chat_order_user_position" in upgrade
    assert (
        "ALTER TABLE group_chats ADD COLUMN IF NOT EXISTS dissolved_at TIMESTAMPTZ"
        in upgrade
    )
    assert "DROP TABLE IF EXISTS chat_order" in downgrade
    assert "DROP COLUMN IF EXISTS dissolved_at" in downgrade
    downs = [
        f.name
        for f in (REPO / "alembic" / "versions").glob("*.py")
        if re.search(r'^down_revision = "c067"$', f.read_text(encoding="utf-8"), re.M)
    ]
    assert downs == ["c068_chat_order_group_dissolve.py"], (
        "c067 之後只能有一個 migration"
    )

    schema = _read("core/database/schema.py")
    assert '("chat_order", create_chat_order_table)' in schema
    assert "CREATE TABLE IF NOT EXISTS chat_order" in schema
    assert re.search(r"dissolved_at\s+TIMESTAMPTZ,", schema), "新 DB 的 CREATE 也要有"
    models = _read("core/orm/models.py")
    assert '__tablename__ = "chat_order"' in models
    assert "dissolved_at: Mapped[Optional[datetime]]" in models


def test_dissolved_at_added_in_create_path_and_safe_reconcile():
    """既有 DB 的 CREATE TABLE IF NOT EXISTS 是 no-op：建表那步與啟動 reconcile 都要補欄位"""
    from unittest.mock import MagicMock

    from core.database.schema import (
        SAFE_RECONCILE_STEPS,
        create_group_chat_tables,
        reconcile_existing_tables,
    )

    assert "group_chats" in dict(SAFE_RECONCILE_STEPS)
    for fn in (create_group_chat_tables, reconcile_existing_tables):
        c = MagicMock()
        fn(c)
        assert any(
            "ALTER TABLE group_chats ADD COLUMN IF NOT EXISTS dissolved_at"
            in call.args[0]
            for call in c.execute.call_args_list
        ), fn.__name__


# ── repo ──────────────────────────────────────────────


async def test_dissolve_removes_everyone_keeps_messages(dpg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = dpg
    gid, invite_id, msg_id = await _setup(s, u, make)

    result = await group_chat_repo.dissolve_group(gid, u["a"], session=s)

    assert result["success"] is True, result
    assert result["group_name"] == "投資閒聊"
    assert result["member_ids"] == [u["a"], u["b"], u["c"]]
    assert result["cancelled_invites"] == [
        {"invite_id": invite_id, "invitee_id": u["d"]}
    ]
    assert result["system_message"]["content"] == f"dissolved:{u['a']}"
    assert result["system_message"]["system_name"] == u["a"], "系統訊息要帶群主名字"

    assert await group_chat_repo.member_ids(gid, session=s) == []
    group = await _row(
        s, "SELECT dissolved_at, owner_id FROM group_chats WHERE id = :g", g=gid
    )
    assert group is not None, "不能刪群（訊息與檢舉會被 CASCADE 刪掉）"
    assert group.dissolved_at is not None and group.owner_id is None
    invite = await _row(
        s, "SELECT status, responded_at FROM group_invites WHERE id = :i", i=invite_id
    )
    assert invite.status == "cancelled" and invite.responded_at is not None
    kept = await _row(s, "SELECT content FROM group_messages WHERE id = :m", m=msg_id)
    assert kept.content == "保證獲利", "訊息要留著當檢舉證據"
    last = await _row(
        s,
        "SELECT content, message_type FROM group_messages WHERE group_id = :g "
        "ORDER BY id DESC LIMIT 1",
        g=gid,
    )
    assert tuple(last) == (f"dissolved:{u['a']}", "system")

    for key in "abc":
        assert await group_chat_repo.list_groups(u[key], session=s) == []
        assert await group_chat_repo.get_group(gid, u[key], session=s) is None
    assert await group_chat_repo.list_invites(u["d"], session=s) == []


async def test_dissolved_group_does_not_use_create_quota(dpg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = dpg
    await make.config("limit_group_create", "1")
    gid = await _group(s, u["a"])
    full = await group_chat_repo.create_group(u["a"], "第二群", session=s)
    assert full == {"success": False, "error": "create_limit_reached"}

    assert (await group_chat_repo.dissolve_group(gid, u["a"], session=s))["success"]
    again = await group_chat_repo.create_group(u["a"], "第二群", session=s)
    assert again["success"] is True, "解散的群不能佔開群名額"


async def test_create_quota_ignores_dissolved_even_if_owner_kept(dpg):
    """開群上限另外排除 dissolved_at（owner_id 沒清掉時的保險）"""
    from sqlalchemy import text

    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = dpg
    await make.config("limit_group_create", "1")
    gid = await _group(s, u["a"])
    await s.execute(
        text("UPDATE group_chats SET dissolved_at = NOW() WHERE id = :g"), {"g": gid}
    )
    again = await group_chat_repo.create_group(u["a"], "第二群", session=s)
    assert again["success"] is True


async def test_dissolve_permissions(dpg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = dpg
    gid = await _group(s, u["a"])
    await _join(s, make, gid, u["a"], u["b"])

    not_owner = await group_chat_repo.dissolve_group(gid, u["b"], session=s)
    assert not_owner == {"success": False, "error": "not_owner"}
    outsider = await group_chat_repo.dissolve_group(gid, u["c"], session=s)
    assert outsider == {"success": False, "error": "not_found"}
    missing = await group_chat_repo.dissolve_group(2_000_000_000, u["a"], session=s)
    assert missing == {"success": False, "error": "not_found"}
    assert await group_chat_repo.member_ids(gid, session=s) == [u["a"], u["b"]], (
        "被拒絕時不能動到成員"
    )

    assert (await group_chat_repo.dissolve_group(gid, u["a"], session=s))["success"]
    twice = await group_chat_repo.dissolve_group(gid, u["a"], session=s)
    assert twice == {"success": False, "error": "not_found"}


async def test_cannot_accept_or_invite_into_dissolved_group(dpg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = dpg
    gid, invite_id, _ = await _setup(s, u, make)
    assert (await group_chat_repo.dissolve_group(gid, u["a"], session=s))["success"]

    accepted = await group_chat_repo.accept_invite(invite_id, u["d"], session=s)
    assert accepted == {"success": False, "error": "invite_not_found"}
    invited = await group_chat_repo.create_invites(gid, u["a"], [u["e"]], session=s)
    assert invited == {"success": False, "error": "not_found"}
    assert await group_chat_repo.member_ids(gid, session=s) == []


async def test_dissolved_group_is_gone_even_with_stray_rows(dpg):
    """解散後若還有漏網的成員列／待處理邀請（併發、手動資料），各路徑照樣當群不存在"""
    from sqlalchemy import text

    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = dpg
    gid = await _group(s, u["a"])
    await make.friends(u["a"], u["d"])
    assert (await group_chat_repo.dissolve_group(gid, u["a"], session=s))["success"]
    await s.execute(
        text("INSERT INTO group_members (group_id, user_id) VALUES (:g, :u)"),
        {"g": gid, "u": u["a"]},
    )
    invite_id = (
        await s.execute(
            text(
                "INSERT INTO group_invites (group_id, inviter_id, invitee_id) "
                "VALUES (:g, :a, :d) RETURNING id"
            ),
            {"g": gid, "a": u["a"], "d": u["d"]},
        )
    ).scalar_one()

    assert await group_chat_repo.list_invites(u["d"], session=s) == []
    accepted = await group_chat_repo.accept_invite(invite_id, u["d"], session=s)
    assert accepted == {"success": False, "error": "invite_not_found"}
    invited = await group_chat_repo.create_invites(gid, u["a"], [u["e"]], session=s)
    assert invited == {"success": False, "error": "not_found"}
    renamed = await group_chat_repo.update_group(gid, u["a"], name="復活", session=s)
    assert renamed == {"success": False, "error": "not_found"}
    again = await group_chat_repo.dissolve_group(gid, u["a"], session=s)
    assert again == {"success": False, "error": "not_found"}


async def test_dissolved_group_disappears_from_pins_search_and_messages(dpg):
    from core.orm.chat_pins_repo import chat_pins_repo
    from core.orm.chat_search_repo import chat_search_repo
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = dpg
    gid, _, _ = await _setup(s, u, make)
    for key in "ab":
        assert (await chat_pins_repo.pin(u[key], "group", gid, session=s))["success"]
    assert (await chat_search_repo.search(u["b"], "投資", session=s))["groups"]
    found = await chat_search_repo.search(u["b"], "保證", session=s)
    assert [m["group_id"] for m in found["messages"]] == [gid]

    assert (await group_chat_repo.dissolve_group(gid, u["a"], session=s))["success"]

    for key in "ab":
        assert await chat_pins_repo.list_pins(u[key], session=s) == [], (
            "群組置頂要跟著消失"
        )
    assert (await chat_search_repo.search(u["b"], "投資", session=s))["groups"] == []
    found = await chat_search_repo.search(u["b"], "保證", session=s)
    assert found["messages"] == [], "解散的群訊息不能再被搜到"
    read = await group_messages_repo.get_messages(gid, u["b"], session=s)
    assert read == {"success": False, "error": "not_found"}
    sent = await group_messages_repo.send_message(gid, u["b"], "還在嗎", session=s)
    assert sent == {"success": False, "error": "not_found"}


async def test_admin_report_listing_shows_dissolved(dpg):
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo
    from core.orm.group_reports_repo import group_reports_repo

    s, u, make = dpg
    gid, _, msg_id = await _setup(s, u, make)
    rep = await group_messages_repo.report_message(msg_id, u["c"], "scam", session=s)
    assert rep["success"] is True, rep

    def _mine(listed):
        (row,) = [r for r in listed["reports"] if r["id"] == rep["report_id"]]
        return row

    before = _mine(await group_reports_repo.list_reports(limit=100, session=s))
    assert before["group_dissolved_at"] is None
    assert (await group_chat_repo.dissolve_group(gid, u["a"], session=s))["success"]
    after = _mine(await group_reports_repo.list_reports(limit=100, session=s))
    assert after["group_name"] == "投資閒聊" and after["group_dissolved_at"], (
        "解散後檢舉照樣看得到，並標出已解散"
    )


# ── 路由 ──────────────────────────────────────────────


async def test_dissolve_route_broadcasts_notifies_and_clears_invites(
    monkeypatch, router
):
    calls = []

    async def fake_dissolve(group_id, owner_id):
        calls.append((group_id, owner_id))
        return {
            "success": True,
            "group_name": "投資閒聊",
            "member_ids": ["a", "b", "c"],
            "system_message": {"id": 40, "message_type": "system"},
            "cancelled_invites": [{"invite_id": 11, "invitee_id": "d"}],
        }

    created = []

    async def fake_create(
        user_id, notification_type, title, body, data=None, session=None
    ):
        created.append((user_id, notification_type, body, data))
        return {"id": f"n-{user_id}", "type": notification_type}

    async def fake_resolve(user_id, invite_id):
        return [f"inv-{user_id}-{invite_id}"]

    monkeypatch.setattr(router.group_chat_repo, "dissolve_group", fake_dissolve)
    monkeypatch.setattr(router.notifications_repo, "create_notification", fake_create)
    monkeypatch.setattr(
        router.notifications_repo, "resolve_group_invite_notifications", fake_resolve
    )

    result = await router.dissolve_group.__wrapped__(_req(), 3, {"user_id": "a"})

    assert result == {"success": True}
    assert calls == [(3, "a")]
    for uid in ("a", "b", "c"):
        assert [p for who, p in router._test_pushed if who == uid] == [
            {"type": "group_updated", "group_id": 3, "kind": "dissolved"}
        ], uid
    assert _types(router._test_pushed, "d") == []
    assert [(uid, t) for uid, t, _, _ in created] == [
        ("b", "group_dissolved"),
        ("c", "group_dissolved"),
    ], "原成員都要收到通知，群主自己不用"
    _, _, body, data = created[0]
    assert data == {"group_id": 3, "group_name": "投資閒聊", "by_name": "a"}
    assert "投資閒聊" in body
    assert [uid for uid, _ in router._test_notified] == ["b", "c"]
    assert router._test_read == [("d", ["inv-d-11"])], (
        "邀請取消了，被邀的人鈴鐺裡的接受鈕要拿掉"
    )


@pytest.mark.parametrize(("error", "status"), [("not_owner", 403), ("not_found", 404)])
async def test_dissolve_route_errors(monkeypatch, router, error, status):
    async def fake_dissolve(group_id, owner_id):
        return {"success": False, "error": error}

    monkeypatch.setattr(router.group_chat_repo, "dissolve_group", fake_dissolve)
    with pytest.raises(HTTPException) as exc:
        await router.dissolve_group.__wrapped__(_req(), 3, {"user_id": "b"})
    assert exc.value.status_code == status and exc.value.detail == error
    assert router._test_pushed == [] and router._test_notified == []


def test_dissolve_route_registered():
    from api_server import app

    # 真正註冊的路由表（同 test_frontend_backend_route_contract）
    paths = app.openapi()["paths"]
    assert "post" in paths.get("/api/groups/{group_id}/dissolve", {})
