"""聊天室 AI 助理問答紀錄（core/orm/chat_assistant_history_repo.py、c070）。

重點是「別人收回／自己刪掉／退群，AI 整理過的問答就跟著消失」，以及只有提問的人讀得到。
真 PostgreSQL、整段交易最後 rollback（同 test_chat_assistant_repo）。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ── 三處同步 ──────────────────────────────────────────────


def test_migration_schema_and_models_in_sync():
    src = _read("alembic/versions/c070_chat_assistant_history.py")
    assert re.search(r'^revision = "c070"$', src, re.M)
    assert re.search(r'^down_revision = "c069"$', src, re.M)
    upgrade, downgrade = src.split("def downgrade")
    assert "CREATE TABLE IF NOT EXISTS chat_assistant_history" in upgrade
    assert "ON DELETE CASCADE" in upgrade
    assert "USING GIN (source_ids)" in upgrade
    assert "DROP TABLE IF EXISTS chat_assistant_history" in downgrade

    schema = _read("core/database/schema.py")
    assert '("chat_assistant_history", create_chat_assistant_history_table)' in schema
    assert "CREATE TABLE IF NOT EXISTS chat_assistant_history" in schema
    assert "USING GIN (source_ids)" in schema
    models = _read("core/orm/models.py")
    assert '__tablename__ = "chat_assistant_history"' in models
    assert "source_ids: Mapped[list[int]]" in models




# ── 小工具 ──────────────────────────────────────────────


async def _dm(s, a, b, content):
    from core.orm.messages_repo import messages_repo

    result = await messages_repo.send_message(a, b, content, session=s)
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


async def _gsend(s, gid, uid, content):
    from core.orm.group_messages_repo import group_messages_repo

    result = await group_messages_repo.send_message(gid, uid, content, session=s)
    assert result["success"] is True, result
    return result["message"]


async def _save(s, kind, target, uid, ids, question="問", answer="答", **kw):
    from core.orm.chat_assistant_history_repo import chat_assistant_history_repo

    return await chat_assistant_history_repo.save_turn(
        kind, target, uid, question, answer, ids, {"model": "Lite"}, session=s, **kw
    )


async def _questions(s, kind, target, uid):
    from core.orm.chat_assistant_history_repo import chat_assistant_history_repo

    result = await chat_assistant_history_repo.list_turns(kind, target, uid, session=s)
    assert result["success"] is True, result
    return [t["question"] for t in result["turns"]]


async def _count(s, uid=None):
    sql = "SELECT count(*) FROM chat_assistant_history"
    params = {}
    if uid:
        sql += " WHERE user_id = :u"
        params = {"u": uid}
    return (await s.execute(text(sql), params)).scalar_one()


def _repo():
    from core.orm.chat_assistant_history_repo import chat_assistant_history_repo

    return chat_assistant_history_repo


# ── 存、讀 ──────────────────────────────────────────────


async def test_save_and_list_oldest_first_only_mine(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    m = await _dm(s, u["a"], u["b"], "BTC 要回測")
    conv = m["conversation_id"]
    first = await _save(s, "dm", conv, u["a"], [m["id"]], question="第一題")
    await _save(s, "dm", conv, u["a"], [m["id"]], question="第二題")
    await _save(s, "dm", conv, u["b"], [m["id"]], question="對方的題")

    assert await _questions(s, "dm", conv, u["a"]) == ["第一題", "第二題"]
    assert await _questions(s, "dm", conv, u["b"]) == ["對方的題"]
    assert first["answer"] == "答" and first["meta"] == {"model": "Lite"}
    # 不是參與者：連有沒有紀錄都不透露
    assert await _repo().list_turns("dm", conv, u["c"], session=s) == {
        "success": False,
        "error": "not_found",
    }


async def test_keeps_latest_ten_and_seven_days(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    m = await _dm(s, u["a"], u["b"], "嗨")
    conv = m["conversation_id"]
    for i in range(12):
        await _save(s, "dm", conv, u["a"], [], question=f"q{i}")
    assert await _questions(s, "dm", conv, u["a"]) == [f"q{i}" for i in range(2, 12)]
    assert await _count(s, u["a"]) == 10, "超過的要真的刪掉，不只是不顯示"

    # 超過保留天數：讀的時候就不給（排程之後才真的刪）
    old = datetime.now(timezone.utc) - timedelta(days=8)
    await _save(s, "dm", conv, u["b"], [], question="舊的", now=old)
    assert await _questions(s, "dm", conv, u["b"]) == []


async def test_purge_expired(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    m = await _dm(s, u["a"], u["b"], "嗨")
    conv = m["conversation_id"]
    old = datetime.now(timezone.utc) - timedelta(days=8)
    await _save(s, "dm", conv, u["a"], [], question="舊", now=old)
    await _save(s, "dm", conv, u["a"], [], question="新")
    purged = await _repo().purge_expired(session=s)
    assert purged >= 1
    assert await _questions(s, "dm", conv, u["a"]) == ["新"]
    assert await _count(s, u["a"]) == 1


async def test_delete_turn_and_clear_only_mine(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    m = await _dm(s, u["a"], u["b"], "嗨")
    conv = m["conversation_id"]
    mine = await _save(s, "dm", conv, u["a"], [], question="我的")
    theirs = await _save(s, "dm", conv, u["b"], [], question="對方的")
    assert await _repo().delete_turn(theirs["id"], u["a"], session=s) is False
    assert await _repo().delete_turn(mine["id"], u["a"], session=s) is True
    assert await _repo().delete_turn(mine["id"], u["a"], session=s) is False
    await _save(s, "dm", conv, u["a"], [], question="又一題")
    assert await _repo().clear("dm", conv, u["a"], session=s) == 1
    assert await _questions(s, "dm", conv, u["b"]) == ["對方的"]


# ── 私訊：收回、隱藏、刪對話 ──────────────────────────────────


async def test_dm_recall_removes_turns_that_used_it_for_everyone(gc_pg):
    from core.orm.messages_repo import messages_repo

    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    m1 = await _dm(s, u["a"], u["b"], "我的私密想法")
    m2 = await _dm(s, u["b"], u["a"], "另一則")
    conv = m1["conversation_id"]
    await _save(s, "dm", conv, u["a"], [m1["id"], m2["id"]], question="a 用了 m1")
    await _save(s, "dm", conv, u["b"], [m1["id"], m2["id"]], question="b 用了 m1")
    await _save(s, "dm", conv, u["b"], [m2["id"]], question="b 只用 m2")

    recalled = await messages_repo.recall_message(m1["id"], u["a"], session=s)
    assert recalled["success"] is True, recalled

    assert await _questions(s, "dm", conv, u["a"]) == []
    assert await _questions(s, "dm", conv, u["b"]) == ["b 只用 m2"]


async def test_save_refused_when_source_was_recalled_meanwhile(gc_pg):
    """產生答案那幾十秒裡有人收回：存的時候要看得到，不能把剛收回的內容存進去"""
    from core.orm.messages_repo import messages_repo

    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    m1 = await _dm(s, u["a"], u["b"], "要收回的")
    m2 = await _dm(s, u["b"], u["a"], "留著的")
    conv = m1["conversation_id"]
    await messages_repo.recall_message(m1["id"], u["a"], session=s)

    assert await _save(s, "dm", conv, u["b"], [m1["id"], m2["id"]]) is None
    assert await _save(s, "dm", conv, u["b"], [m2["id"]]) is not None
    assert await _count(s, u["b"]) == 1


async def test_hide_only_removes_my_turns(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    m1 = await _dm(s, u["a"], u["b"], "嗨")
    conv = m1["conversation_id"]
    await _save(s, "dm", conv, u["a"], [m1["id"]], question="a")
    await _save(s, "dm", conv, u["b"], [m1["id"]], question="b")
    assert (
        await _repo().invalidate_message("dm", m1["id"], user_id=u["b"], session=s) == 1
    )
    assert await _questions(s, "dm", conv, u["a"]) == ["a"]
    assert await _questions(s, "dm", conv, u["b"]) == []


async def test_delete_conversation_only_clears_mine(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    m1 = await _dm(s, u["a"], u["b"], "嗨")
    conv = m1["conversation_id"]
    await _save(s, "dm", conv, u["a"], [], question="a")
    await _save(s, "dm", conv, u["b"], [], question="b")
    assert await _repo().invalidate_chat("dm", conv, user_id=u["a"], session=s) == 1
    assert await _questions(s, "dm", conv, u["b"]) == ["b"]


async def test_dm_and_group_message_ids_do_not_collide(gc_pg):
    """kind 要一起比：dm 訊息 5 被收回，不能刪到讀過「群組訊息 5」的問答"""
    s, u, make = gc_pg
    gid = await _group(s, u, make, "b")
    g = await _gsend(s, gid, u["a"], "群組訊息")
    await _save(s, "group", gid, u["a"], [g["id"]], question="群組的問")
    assert await _repo().invalidate_message("dm", g["id"], session=s) == 0
    assert await _questions(s, "group", gid, u["a"]) == ["群組的問"]


# ── 群組：收回、退群、被移出、解散 ────────────────────────────


async def test_group_recall_removes_turns_that_used_it(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, "b", "c")
    g1 = await _gsend(s, gid, u["a"], "要收回")
    g2 = await _gsend(s, gid, u["b"], "留著")
    await _save(s, "group", gid, u["b"], [g1["id"], g2["id"]], question="b 用了 g1")
    await _save(s, "group", gid, u["c"], [g2["id"]], question="c 只用 g2")

    recalled = await group_messages_repo.recall_message(g1["id"], u["a"], session=s)
    assert recalled["success"] is True, recalled
    assert await _questions(s, "group", gid, u["b"]) == []
    assert await _questions(s, "group", gid, u["c"]) == ["c 只用 g2"]


async def test_group_leave_clears_only_the_leaver(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, "b", "c")
    g1 = await _gsend(s, gid, u["a"], "嗨")
    for who in ("a", "b", "c"):
        await _save(s, "group", gid, u[who], [g1["id"]], question=who)

    assert (await group_chat_repo.leave_group(gid, u["b"], session=s))["success"]
    assert await _questions(s, "group", gid, u["a"]) == ["a"]
    assert await _questions(s, "group", gid, u["c"]) == ["c"]
    # 退群的人已經看不到這個群：讀不到，也存不進去（產生答案中途退群）
    assert (await _repo().list_turns("group", gid, u["b"], session=s))[
        "success"
    ] is False
    assert await _save(s, "group", gid, u["b"], [g1["id"]]) is None
    assert await _count(s, u["b"]) == 0


async def test_group_remove_member_clears_the_removed(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, "b", "c")
    g1 = await _gsend(s, gid, u["a"], "嗨")
    for who in ("a", "b", "c"):
        await _save(s, "group", gid, u[who], [g1["id"]], question=who)

    removed = await group_chat_repo.remove_member(gid, u["a"], u["c"], session=s)
    assert removed["success"] is True, removed
    assert await _count(s, u["c"]) == 0
    assert await _questions(s, "group", gid, u["a"]) == ["a"]
    assert await _questions(s, "group", gid, u["b"]) == ["b"]


async def test_group_dissolve_clears_everyone(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, "b", "c")
    g1 = await _gsend(s, gid, u["a"], "嗨")
    for who in ("a", "b", "c"):
        await _save(s, "group", gid, u[who], [g1["id"]], question=who)

    dissolved = await group_chat_repo.dissolve_group(gid, u["a"], session=s)
    assert dissolved["success"] is True, dissolved
    for who in ("a", "b", "c"):
        assert await _count(s, u[who]) == 0, who


# ── 資料表本身 ──────────────────────────────────────────────


async def test_table_rules(gc_pg):
    s, u, _make = gc_pg
    rule = (
        await s.execute(
            text(
                """
                SELECT rc.delete_rule
                FROM information_schema.referential_constraints rc
                JOIN information_schema.table_constraints tc
                  ON tc.constraint_name = rc.constraint_name
                WHERE tc.table_name = 'chat_assistant_history'
                """
            )
        )
    ).scalar_one()
    assert rule == "CASCADE", "刪帳號時問答要一起刪"

    nested = await s.begin_nested()
    with pytest.raises(Exception):  # noqa: B017 — CHECK 擋掉 kind 寫錯
        await s.execute(
            text(
                "INSERT INTO chat_assistant_history (user_id, kind, target_id, question, answer) "
                "VALUES (:u, 'channel', 1, 'q', 'a')"
            ),
            {"u": u["a"]},
        )
    await nested.rollback()
