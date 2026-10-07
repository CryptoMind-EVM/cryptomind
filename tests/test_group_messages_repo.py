"""群組聊天：訊息、已讀、表情、收回、檢舉（core/orm/group_messages_repo.py）。

重點是「可見範圍」：成員只看得到 id > first_visible_message_id 的訊息——讀取、往上翻、
回覆目標、表情、檢舉快照都不能越界。真 PostgreSQL、交易最後 rollback。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）


async def _setup(s, u, make, *members):
    """a 開群，members 一個個入群（預設看不到入群前的訊息）"""
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


async def _send(s, gid, uid, content, **kw):
    from core.orm.group_messages_repo import group_messages_repo

    result = await group_messages_repo.send_message(gid, uid, content, session=s, **kw)
    assert result["success"] is True, result
    return result["message"]


# ── 送出 ──────────────────────────────────────────────


async def test_send_message_shape_and_members(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b")
    result = await group_messages_repo.send_message(gid, u["b"], "  早安  ", session=s)
    msg = result["message"]
    assert (
        msg["content"] == "早安"
        and msg["group_id"] == gid
        and msg["from_user_id"] == u["b"]
    )
    assert msg["from_username"] == u["b"] and msg["message_type"] == "text"
    assert msg["reactions"] == [] and msg["reply_to"] is None
    assert sorted(result["member_ids"]) == sorted([u["a"], u["b"]])
    me = (await group_chat_repo.get_group(gid, u["b"], session=s))["me"]
    assert me["last_read_message_id"] == msg["id"], "自己送的訊息算已讀"


async def test_send_rules(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b")
    send = group_messages_repo.send_message
    assert (await send(gid, u["c"], "hi", session=s))["error"] == "not_found"
    assert (await send(gid, u["b"], "   ", session=s))["error"] == "empty_content"
    await make.config("limit_message_max_length", "5")
    assert (await send(gid, u["b"], "123456", session=s))["error"] == "message_too_long"


async def test_reply_target_must_be_same_group_and_visible(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make)
    old = await _send(s, gid, u["a"], "c 入群前")
    await _setup_join(s, u, make, gid, "c")
    other_gid = await _setup(s, u, make)
    other = await _send(s, other_gid, u["a"], "別群")

    send = group_messages_repo.send_message
    assert (await send(gid, u["c"], "回", reply_to_message_id=old["id"], session=s))[
        "error"
    ] == ("reply_target_not_found"), "看不到的訊息不能回覆"
    assert (await send(gid, u["a"], "回", reply_to_message_id=other["id"], session=s))[
        "error"
    ] == ("reply_target_not_found")
    target = await _send(s, gid, u["a"], "可以回")
    reply = await _send(s, gid, u["c"], "回你", reply_to_message_id=target["id"])
    assert (
        reply["reply_to"]["id"] == target["id"]
        and reply["reply_to"]["snippet"] == "可以回"
    )

    await group_messages_repo.recall_message(target["id"], u["a"], session=s)
    assert (await send(gid, u["c"], "回", reply_to_message_id=target["id"], session=s))[
        "error"
    ] == ("reply_target_recalled")


async def _setup_join(s, u, make, gid, member):
    from core.orm.group_chat_repo import group_chat_repo

    await make.friends(u["a"], u[member])
    inv = await group_chat_repo.create_invites(gid, u["a"], [u[member]], session=s)
    await group_chat_repo.accept_invite(
        inv["invited"][0]["invite_id"], u[member], session=s
    )


# ── 讀取 ──────────────────────────────────────────────


async def test_get_messages_respects_visibility_and_paging(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make)
    await _send(s, gid, u["a"], "入群前 1")
    await _send(s, gid, u["a"], "入群前 2")
    await _setup_join(s, u, make, gid, "b")
    for i in range(3):
        await _send(s, gid, u["a"], f"之後 {i}")

    got = await group_messages_repo.get_messages(gid, u["b"], session=s)
    contents = [m["content"] for m in got["messages"]]
    assert "入群前 1" not in contents and "入群前 2" not in contents
    assert contents == [f"member_joined:{u['b']}", "之後 0", "之後 1", "之後 2"]
    assert got["has_more"] is False

    page = await group_messages_repo.get_messages(gid, u["b"], limit=2, session=s)
    assert [m["content"] for m in page["messages"]] == ["之後 1", "之後 2"] and page[
        "has_more"
    ] is True
    older = await group_messages_repo.get_messages(
        gid, u["b"], limit=10, before_id=page["messages"][0]["id"], session=s
    )
    assert [m["content"] for m in older["messages"]] == [
        f"member_joined:{u['b']}",
        "之後 0",
    ]

    full = await group_messages_repo.get_messages(gid, u["a"], session=s)
    assert "入群前 1" in [m["content"] for m in full["messages"]], "群主從頭都看得到"
    assert (await group_messages_repo.get_messages(gid, u["c"], session=s))[
        "error"
    ] == "not_found"


# ── 已讀 ──────────────────────────────────────────────


async def test_mark_read_never_goes_backwards_or_past_latest(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b")
    m1 = await _send(s, gid, u["a"], "1")
    m2 = await _send(s, gid, u["a"], "2")

    r = await group_messages_repo.mark_read(gid, u["b"], m2["id"], session=s)
    assert r["changed"] is True and r["last_read_message_id"] == m2["id"]
    back = await group_messages_repo.mark_read(gid, u["b"], m1["id"], session=s)
    assert back["changed"] is False and back["last_read_message_id"] == m2["id"], (
        "不能倒退"
    )
    future = await group_messages_repo.mark_read(
        gid, u["b"], m2["id"] + 1000, session=s
    )
    assert future["last_read_message_id"] == m2["id"], "不能超過群組最新一則"
    assert (await group_messages_repo.mark_read(gid, u["c"], m2["id"], session=s))[
        "error"
    ] == "not_found"


# ── 收回 ──────────────────────────────────────────────


async def test_recall_rules(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b")
    msg = await _send(s, gid, u["b"], "傳錯了")
    await group_messages_repo.set_reaction(msg["id"], u["a"], "haha", session=s)

    recall = group_messages_repo.recall_message
    assert (await recall(msg["id"], u["c"], session=s))["error"] == "message_not_found"
    assert (await recall(msg["id"], u["a"], session=s))["error"] == "permission_denied"
    late = datetime.now(timezone.utc) + timedelta(hours=25)
    assert (await recall(msg["id"], u["b"], now=late, session=s))[
        "error"
    ] == "recall_window_expired"

    result = await recall(msg["id"], u["b"], session=s)
    assert result["success"] is True and result["group_id"] == gid
    assert sorted(result["member_ids"]) == sorted([u["a"], u["b"]])
    (row,) = [
        m
        for m in (await group_messages_repo.get_messages(gid, u["a"], session=s))[
            "messages"
        ]
        if m["id"] == msg["id"]
    ]
    assert (
        row["content"] == ""
        and row["message_type"] == "recalled"
        and row["reactions"] == []
    )
    assert (await recall(msg["id"], u["b"], session=s))["error"] == "already_recalled"


async def test_system_messages_cannot_be_recalled_reacted_or_reported(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b")
    system = (await group_messages_repo.get_messages(gid, u["a"], session=s))[
        "messages"
    ][0]
    assert system["message_type"] == "system"
    assert (await group_messages_repo.recall_message(system["id"], u["a"], session=s))[
        "error"
    ] == "permission_denied"
    assert (
        await group_messages_repo.set_reaction(system["id"], u["a"], "like", session=s)
    )["error"] == ("message_not_found")
    assert (
        await group_messages_repo.report_message(
            system["id"], u["b"], "spam", session=s
        )
    )["error"] == ("message_not_found")


# ── 表情 ──────────────────────────────────────────────


async def test_reactions(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make)
    before = await _send(s, gid, u["a"], "c 看不到")
    await _setup_join(s, u, make, gid, "b")
    await _setup_join(s, u, make, gid, "c")
    msg = await _send(s, gid, u["a"], "好消息")

    r = await group_messages_repo.set_reaction(msg["id"], u["b"], "rocket", session=s)
    assert r["success"] is True and r["group_id"] == gid
    assert r["reactions"] == [{"user_id": u["b"], "reaction": "rocket"}]
    await group_messages_repo.set_reaction(msg["id"], u["c"], "love", session=s)
    r = await group_messages_repo.set_reaction(msg["id"], u["b"], "ok", session=s)
    assert {x["user_id"]: x["reaction"] for x in r["reactions"]} == {
        u["b"]: "ok",
        u["c"]: "love",
    }, "按別的＝替換"

    removed = await group_messages_repo.remove_reaction(msg["id"], u["b"], session=s)
    assert removed["changed"] is True and removed["reactions"] == [
        {"user_id": u["c"], "reaction": "love"}
    ]
    assert (await group_messages_repo.remove_reaction(msg["id"], u["b"], session=s))[
        "changed"
    ] is False

    assert (
        await group_messages_repo.set_reaction(msg["id"], u["b"], "nope", session=s)
    )["error"] == "invalid_reaction"
    assert (
        await group_messages_repo.set_reaction(before["id"], u["c"], "like", session=s)
    )["error"] == ("message_not_found"), "看不到的訊息不能按"
    assert (
        await group_messages_repo.set_reaction(msg["id"], u["d"], "like", session=s)
    )["error"] == "message_not_found"


# ── 檢舉 ──────────────────────────────────────────────


async def test_report_snapshot_stays_within_reporters_view(gc_pg):
    from sqlalchemy import text

    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b")
    for i in range(3):
        await _send(s, gid, u["a"], f"c 入群前 {i}")
    await _setup_join(s, u, make, gid, "c")
    for i in range(12):
        await _send(s, gid, u["b"], f"之後 {i}")
    target = await _send(s, gid, u["b"], "快來投資保證獲利")

    report = group_messages_repo.report_message
    assert (await report(target["id"], u["b"], "scam", session=s))[
        "error"
    ] == "cannot_report_self"
    assert (await report(target["id"], u["d"], "scam", session=s))[
        "error"
    ] == "message_not_found"
    result = await report(target["id"], u["c"], "scam", note="詐騙", session=s)
    assert result["success"] is True and result["reported_user_id"] == u["b"]
    assert (await report(target["id"], u["c"], "scam", session=s))[
        "error"
    ] == "already_reported"

    snapshot = (
        await s.execute(
            text("SELECT snapshot FROM group_reports WHERE id = :i"),
            {"i": result["report_id"]},
        )
    ).scalar_one()
    contents = [m["content"] for m in snapshot]
    assert contents[-1] == "快來投資保證獲利" and len(contents) == 11, (
        "被檢舉那則＋前 10 則"
    )
    assert not any(c.startswith("c 入群前") for c in contents), (
        "快照不能越過檢舉者看得到的範圍"
    )


async def test_send_updates_group_last_message(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b")
    msg = await _send(s, gid, u["a"], "最新")
    (row,) = await group_chat_repo.list_groups(u["b"], session=s)
    assert row["last_message"]["id"] == msg["id"] and row["last_message_at"] is not None


# ── 通知（notifications_repo 的群組部分）────────────────────


async def test_group_message_notifications_merge_clear_and_recall(gc_pg):
    from core.orm.notifications_repo import notifications_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b")
    notify = notifications_repo.notify_group_message
    first = await notify(
        to_user_id=u["b"],
        group_id=gid,
        group_name="投資閒聊",
        from_user_id=u["a"],
        from_name="小明",
        message_preview="第一則",
        message_id=1,
        session=s,
    )
    second = await notify(
        to_user_id=u["b"],
        group_id=gid,
        group_name="投資閒聊",
        from_user_id=u["a"],
        from_name="小明",
        message_preview="第二則",
        message_id=2,
        session=s,
    )
    assert second["id"] == first["id"], "同一群未讀合併成一筆"
    assert second["data"]["count"] == 2 and second["body"] == "小明: 第二則"
    assert second["title"] == "投資閒聊"

    recalled = await notifications_repo.mark_group_message_recalled(gid, 2, session=s)
    assert [n["body"] for n in recalled] == ["小明: 訊息已收回"], "收回後通知不能留原文"

    cleared = await notifications_repo.mark_group_message_notifications_read(
        u["b"], gid, session=s
    )
    assert cleared == [first["id"]]
    third = await notify(
        to_user_id=u["b"],
        group_id=gid,
        group_name="投資閒聊",
        from_user_id=u["a"],
        from_name="小明",
        message_preview="第三則",
        message_id=3,
        session=s,
    )
    assert third["id"] != first["id"], "讀過之後再來的是新的一筆"


async def test_group_invite_notification_resolved(gc_pg):
    from core.orm.notifications_repo import notifications_repo

    s, u, _ = gc_pg
    n = await notifications_repo.create_notification(
        user_id=u["b"],
        notification_type="group_invite",
        title="群組邀請",
        body="小明邀請你加入",
        data={"invite_id": 11, "group_id": 3},
        session=s,
    )
    assert (
        await notifications_repo.resolve_group_invite_notifications(
            u["b"], 99, session=s
        )
        == []
    )
    assert await notifications_repo.resolve_group_invite_notifications(
        u["b"], 11, session=s
    ) == [n["id"]]


async def test_group_report_risk_score_written_back(gc_pg, monkeypatch):
    from sqlalchemy import text

    from core.moderation import reports
    from core.orm.group_messages_repo import group_messages_repo

    async def fake_risk(content):
        return (
            {"score": 0.91, "category": "investment_scam"}
            if "保證獲利" in content
            else None
        )

    monkeypatch.setattr(reports, "risk_of", fake_risk)
    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b")
    target = await _send(s, gid, u["a"], "保證獲利快加入")
    report = await group_messages_repo.report_message(
        target["id"], u["b"], "scam", session=s
    )
    risk = await reports.score_group_report(
        report["report_id"],
        reports.dm_report_text(report["snapshot"], report["reported_user_id"]),
        session=s,
    )
    assert risk == {"score": 0.91, "category": "investment_scam"}
    row = (
        await s.execute(
            text("SELECT risk_score, risk_category FROM group_reports WHERE id = :i"),
            {"i": report["report_id"]},
        )
    ).one()
    assert row[0] == pytest.approx(0.91) and row[1] == "investment_scam"


async def test_mark_read_never_below_visibility_boundary(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make)
    await _send(s, gid, u["a"], "b 入群前")
    await _setup_join(s, u, make, gid, "b")
    boundary = (await group_chat_repo.get_group(gid, u["b"], session=s))["me"][
        "first_visible_message_id"
    ]
    r = await group_messages_repo.mark_read(gid, u["b"], 1, session=s)
    assert r["last_read_message_id"] >= boundary, (
        "讀取位置不能落在看不到的範圍（別人的「已讀 N」會算錯）"
    )


async def test_system_messages_carry_display_names(gc_pg):
    """系統訊息帶當事人名字（system_name）：對方不在成員名單（還沒載入、已經退群）也顯示得出名字"""
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make)
    await make.friends(u["a"], u["b"])
    inv = await group_chat_repo.create_invites(gid, u["a"], [u["b"]], session=s)
    acc = await group_chat_repo.accept_invite(
        inv["invited"][0]["invite_id"], u["b"], session=s
    )
    assert acc["system_message"]["system_name"] == u["b"], (
        "推播出去的系統訊息就要帶名字"
    )
    left = await group_chat_repo.leave_group(gid, u["b"], session=s)
    assert left["system_messages"][0]["system_name"] == u["b"]

    rows = (await group_messages_repo.get_messages(gid, u["a"], session=s))["messages"]
    names = {m["content"].split(":")[0]: m.get("system_name") for m in rows}
    assert names == {
        "created": u["a"],
        "member_joined": u["b"],
        "member_left": u["b"],
    }, "讀取時也要有（b 已經不在群裡）"
    (row,) = await group_chat_repo.list_groups(u["a"], session=s)
    assert row["last_message"]["system_name"] == u["b"], (
        "列表預覽最後一則是系統訊息時也要有"
    )


async def test_mentions_on_send_and_history(gc_pg):
    """@提及：原文照存，渲染時比對當下成員名單；通知對象不含發訊者自己"""
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b", "c")
    result = await group_messages_repo.send_message(
        gid,
        u["a"],
        f"@{u['b']} 看一下，@{u['a']} 自己也算，@{u['d']} 不是成員",
        session=s,
    )
    msg = result["message"]
    assert msg["content"].startswith(f"@{u['b']} "), "原文照存"
    assert [m["user_id"] for m in msg["mentions"]] == [u["b"], u["a"]]
    assert result["mentioned_ids"] == [u["b"]], "自己不通知、非成員不算"

    plain = await _send(s, gid, u["b"], "沒有提及")
    assert plain["mentions"] == []

    history = await group_messages_repo.get_messages(gid, u["c"], session=s)
    by_id = {m["id"]: m for m in history["messages"]}
    assert [m["user_id"] for m in by_id[msg["id"]]["mentions"]] == [u["b"], u["a"]]
    assert all(
        m["mentions"] == []
        for m in history["messages"]
        if m["message_type"] == "system"
    )

    # 退群的人：舊訊息不再標成提及（名單是當下成員）
    await group_chat_repo.leave_group(gid, u["b"], session=s)
    history = await group_messages_repo.get_messages(gid, u["c"], session=s)
    by_id = {m["id"]: m for m in history["messages"]}
    assert [m["user_id"] for m in by_id[msg["id"]]["mentions"]] == [u["a"]]


async def test_mention_flag_on_group_notification(gc_pg):
    """被提及：合併的那筆群組通知標 mentioned（之後一般訊息合併進來也不掉），讀了群就清掉"""
    from core.orm.notifications_repo import notifications_repo

    s, u, make = gc_pg
    gid = await _setup(s, u, make, "b")
    other = await _setup(s, u, make)
    base = dict(
        to_user_id=u["b"],
        group_id=gid,
        group_name="投資閒聊",
        from_user_id=u["a"],
        from_name="小明",
        session=s,
    )
    notify = notifications_repo.notify_group_message
    first = await notify(**base, message_preview="一般", message_id=1)
    assert "mentioned" not in first["data"]
    assert await notifications_repo.unread_mention_group_ids(u["b"], session=s) == set()

    second = await notify(
        **base, message_preview=f"@{u['b']} 看", message_id=2, mentioned=True
    )
    assert second["id"] == first["id"] and second["data"]["mentioned"] is True
    third = await notify(**base, message_preview="又一則", message_id=3)
    assert third["data"]["mentioned"] is True, "後面一般訊息合併進來，提及標記不能掉"
    assert await notifications_repo.unread_mention_group_ids(u["b"], session=s) == {gid}
    assert (
        await notifications_repo.unread_mention_group_ids(u["a"], session=s) == set()
    ), "別人的不算"
    assert other not in await notifications_repo.unread_mention_group_ids(
        u["b"], session=s
    )

    await notifications_repo.mark_group_message_notifications_read(
        u["b"], gid, session=s
    )
    assert await notifications_repo.unread_mention_group_ids(u["b"], session=s) == set()
