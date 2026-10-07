"""聊天室 AI 助理：撈訊息與次數（core/orm/chat_assistant_repo.py）。

重點是「只給 AI 看提問者自己看得到的訊息」：私訊排除自己刪的、收回的；群組只到
first_visible_message_id 之後；unread 的 from_id 是前端給的，只當下限。真 PostgreSQL、rollback。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）


# ── 小工具 ──────────────────────────────────────────────


async def _dm(s, a, b, content, **kw):
    from core.orm.messages_repo import messages_repo

    result = await messages_repo.send_message(a, b, content, session=s, **kw)
    assert result["success"] is True, result
    return result["message"]


async def _group(s, u, make, *members):
    from core.orm.group_chat_repo import group_chat_repo

    gid = (await group_chat_repo.create_group(u["a"], "投資閒聊", session=s))["group"][
        "id"
    ]
    for m in members:
        await make.friends(u["a"], u[m])
        inv = await group_chat_repo.create_invites(gid, u["a"], [u[m]], session=s)
        await group_chat_repo.accept_invite(
            inv["invited"][0]["invite_id"], u[m], session=s
        )
    return gid


async def _gsend(s, gid, uid, content, **kw):
    from core.orm.group_messages_repo import group_messages_repo

    result = await group_messages_repo.send_message(gid, uid, content, session=s, **kw)
    assert result["success"] is True, result
    return result["message"]


async def _fetch(s, kind, target, uid, range_="recent", **kw):
    from core.orm.chat_assistant_repo import chat_assistant_repo

    return await chat_assistant_repo.fetch_messages(
        kind, target, uid, range_, session=s, **kw
    )


def _texts(result):
    assert result["success"] is True, result
    return [m["text"] for m in result["messages"]]


# ── 設定列 ──────────────────────────────────────────────


def test_default_config_rows_seeded():
    """init_default_data 會補上開關（預設關）與非 Pro 每日次數（預設 5）"""
    from core.database.schema import init_default_data

    inserted = {}

    class _Cursor:
        def execute(self, sql, params=None):
            if params and "INSERT INTO system_config" in sql:
                inserted[params[0]] = params

        def fetchone(self):
            return (0,)

    init_default_data(_Cursor())
    assert inserted["chat_assistant_enabled"][1:4] == ("false", "bool", "features")
    assert inserted["limit_ai_assistant_free_daily"][1:4] == ("5", "int", "limits")


# ── 私訊 ──────────────────────────────────────────────


async def test_dm_only_participants(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    m = await _dm(s, u["a"], u["b"], "嗨")
    conv = m["conversation_id"]
    assert _texts(await _fetch(s, "dm", conv, u["b"])) == ["嗨"]
    assert (await _fetch(s, "dm", conv, u["c"])) == {
        "success": False,
        "error": "not_found",
    }


async def test_dm_names_and_order(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    await s.execute(
        text("UPDATE users SET display_name='小明' WHERE user_id=:u"), {"u": u["a"]}
    )
    first = await _dm(s, u["a"], u["b"], "第一則")
    await _dm(s, u["b"], u["a"], "第二則", reply_to_message_id=first["id"])
    msgs = (await _fetch(s, "dm", first["conversation_id"], u["a"]))["messages"]
    assert [m["text"] for m in msgs] == ["第一則", "第二則"]
    assert msgs[0]["name"] == "小明" and msgs[1]["name"] == u["b"]
    assert msgs[1]["reply_to_name"] == "小明"
    assert "from_user_id" not in msgs[0], "不給模型 user id"


async def test_dm_hides_recalled_and_deleted_for_me(gc_pg):
    from core.orm.messages_repo import messages_repo

    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    keep = await _dm(s, u["a"], u["b"], "留著")
    recalled = await _dm(s, u["a"], u["b"], "收回的秘密")
    deleted = await _dm(s, u["b"], u["a"], "b 自己刪的")
    assert (await messages_repo.recall_message(recalled["id"], u["a"], session=s))[
        "success"
    ]
    await s.execute(
        text("INSERT INTO dm_message_deletions (message_id, user_id) VALUES (:m, :u)"),
        {"m": deleted["id"], "u": u["b"]},
    )
    conv = keep["conversation_id"]
    assert _texts(await _fetch(s, "dm", conv, u["b"])) == ["留著"]
    # 刪除只對刪的人隱藏：a 那邊還看得到
    assert _texts(await _fetch(s, "dm", conv, u["a"])) == ["留著", "b 自己刪的"]


async def test_dm_unread_from_id_and_time_window(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    old = await _dm(s, u["a"], u["b"], "三天前")
    mid = await _dm(s, u["a"], u["b"], "昨天")
    new = await _dm(s, u["a"], u["b"], "剛剛")
    await s.execute(
        text(
            "UPDATE dm_messages SET created_at = now() - interval '50 hours' WHERE id=:i"
        ),
        {"i": old["id"]},
    )
    await s.execute(
        text(
            "UPDATE dm_messages SET created_at = now() - interval '20 hours' WHERE id=:i"
        ),
        {"i": mid["id"]},
    )
    conv = new["conversation_id"]
    assert _texts(await _fetch(s, "dm", conv, u["b"], "unread", from_id=mid["id"])) == [
        "昨天",
        "剛剛",
    ]
    assert _texts(await _fetch(s, "dm", conv, u["b"], "24h")) == ["昨天", "剛剛"]
    assert _texts(await _fetch(s, "dm", conv, u["b"], "3d")) == [
        "三天前",
        "昨天",
        "剛剛",
    ]


async def test_dm_around_message(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    sent = [await _dm(s, u["a"], u["b"], f"m{i}") for i in range(15)]
    conv = sent[0]["conversation_id"]
    got = _texts(
        await _fetch(s, "dm", conv, u["b"], "message", around_id=sent[7]["id"])
    )
    assert got == [f"m{i}" for i in range(2, 13)], "前後各 5 則"
    # 別的對話的訊息 id 不能拿來當 around
    await make.friends(u["c"], u["d"])
    other = await _dm(s, u["c"], u["d"], "別人的")
    assert (await _fetch(s, "dm", conv, u["b"], "message", around_id=other["id"]))[
        "success"
    ] is False


# ── 群組 ──────────────────────────────────────────────


async def test_group_only_members_and_visible_range(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, "b")
    await _gsend(s, gid, u["a"], "c 入群前")
    # c 入群：預設看不到之前的
    await make.friends(u["a"], u["c"])
    inv = await group_chat_repo.create_invites(gid, u["a"], [u["c"]], session=s)
    await group_chat_repo.accept_invite(
        inv["invited"][0]["invite_id"], u["c"], session=s
    )
    await _gsend(s, gid, u["b"], "c 入群後")

    c_texts = _texts(await _fetch(s, "group", gid, u["c"]))
    assert "c 入群前" not in c_texts and c_texts[-1] == "c 入群後"
    # unread 的 from_id 就算給 0 也不會越過可見範圍
    assert "c 入群前" not in _texts(
        await _fetch(s, "group", gid, u["c"], "unread", from_id=0)
    )
    assert (await _fetch(s, "group", gid, u["d"])) == {
        "success": False,
        "error": "not_found",
    }


async def test_group_system_lines_and_recalled(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    await s.execute(
        text("UPDATE users SET display_name='小華' WHERE user_id=:u"), {"u": u["b"]}
    )
    gid = await _group(s, u, make, "b")
    gone = await _gsend(s, gid, u["b"], "不小心講的")
    assert (await group_messages_repo.recall_message(gone["id"], u["b"], session=s))[
        "success"
    ]
    msgs = (await _fetch(s, "group", gid, u["a"]))["messages"]
    texts = [m["text"] for m in msgs]
    assert "不小心講的" not in texts and "" not in texts
    system = [m for m in msgs if m["type"] == "system"]
    assert any(m["text"] == "小華 joined the group" for m in system), system


async def test_group_around_must_be_visible(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, "b")
    before = await _gsend(s, gid, u["a"], "入群前")
    await make.friends(u["a"], u["c"])
    inv = await group_chat_repo.create_invites(gid, u["a"], [u["c"]], session=s)
    await group_chat_repo.accept_invite(
        inv["invited"][0]["invite_id"], u["c"], session=s
    )
    assert (await _fetch(s, "group", gid, u["c"], "message", around_id=before["id"]))[
        "success"
    ] is False


# ── 次數 ──────────────────────────────────────────────


async def test_quota_limit_record_and_refund(gc_pg):
    from core.orm.chat_assistant_repo import chat_assistant_repo as repo

    s, u, _ = gc_pg
    for i in range(1, 3):
        r = await repo.consume_quota(u["a"], 2, session=s)
        assert r == {"ok": True, "used": i, "remaining": 2 - i}
    assert (await repo.consume_quota(u["a"], 2, session=s))["ok"] is False
    assert await repo.used_today(u["a"], session=s) == 2, "被擋的那次不加"
    await repo.refund_quota(u["a"], session=s)
    assert (await repo.consume_quota(u["a"], 2, session=s))["ok"] is True

    # limit=None：只記不擋（Pro 用平台模型）
    for _ in range(3):
        assert (await repo.consume_quota(u["b"], None, session=s))["ok"] is True
    assert await repo.used_today(u["b"], session=s) == 3
    # 退回不會變負數
    for _ in range(5):
        await repo.refund_quota(u["c"], session=s)
    assert await repo.used_today(u["c"], session=s) == 0


async def test_reply_to_recalled_message_has_no_name(gc_pg):
    """回覆的對象被收回了：連「回覆誰」都不給模型（review HIGH）"""
    from core.orm.group_messages_repo import group_messages_repo
    from core.orm.messages_repo import messages_repo

    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    target = await _dm(s, u["b"], u["a"], "等等收回")
    await _dm(s, u["a"], u["b"], "回你", reply_to_message_id=target["id"])
    assert (await messages_repo.recall_message(target["id"], u["b"], session=s))[
        "success"
    ]
    msgs = (await _fetch(s, "dm", target["conversation_id"], u["a"]))["messages"]
    assert [(m["text"], m["reply_to_name"]) for m in msgs] == [("回你", None)]

    gid = await _group(s, u, make, "c")
    gtarget = await _gsend(s, gid, u["c"], "群裡收回")
    await _gsend(s, gid, u["a"], "回 c", reply_to_message_id=gtarget["id"])
    assert (await group_messages_repo.recall_message(gtarget["id"], u["c"], session=s))[
        "success"
    ]
    gmsgs = (await _fetch(s, "group", gid, u["a"]))["messages"]
    assert [m["reply_to_name"] for m in gmsgs if m["text"] == "回 c"] == [None]


async def test_unread_count_counts_incoming_only(gc_pg):
    """私訊的「我沒看的」：前端給打開前的未讀數，後端從對方傳來的訊息往回數"""
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    await _dm(s, u["b"], u["a"], "舊的已讀")
    await _dm(s, u["a"], u["b"], "我說的")
    await _dm(s, u["b"], u["a"], "未讀一")
    await _dm(s, u["a"], u["b"], "我又說")
    last = await _dm(s, u["b"], u["a"], "未讀二")
    conv = last["conversation_id"]
    got = _texts(await _fetch(s, "dm", conv, u["a"], "unread", unread_count=2))
    assert got == ["未讀一", "我又說", "未讀二"], (
        "從第 2 新的來訊開始，中間自己說的也帶上"
    )
    # 未讀數比實際來訊多：從頭開始
    assert (
        _texts(await _fetch(s, "dm", conv, u["a"], "unread", unread_count=99))[0]
        == "舊的已讀"
    )
