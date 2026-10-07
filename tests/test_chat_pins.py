"""對話置頂＋拖曳排序（c067）：社群列表的 chat_pins、AI 對話的 sessions.pin_order。

- repo 部分用真 PostgreSQL、整段交易最後 rollback（tests/group_chat_pg）。
- 「真的有 commit」那幾題自己開連線寫入、用另一條連線讀回、跑完清掉
  （query_one 跑 INSERT 沒 commit 的教訓：同一個 session 讀得到不代表進了 DB）。
- 路由部分把 repo／run_sync 換成假的，走真的 app 路由（順便驗 /order、/pin-order 沒被
  /{kind}/{target_id}、/{session_id} 吃掉）。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from tests import group_chat_pg

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）


@pytest.fixture(scope="module")
def pg_conn_factory():
    """測試庫不跑 alembic：第一次 get_connection() 會跑 init_db（schema.py 建表＋reconcile），
    chat_pins 與 sessions.pin_order 由此補上。連不到就 skip。"""
    from core.database.connection import get_connection

    try:
        get_connection().close()
    except Exception as e:  # noqa: BLE001 — 連不到就 skip，不是測試失敗
        pytest.skip(f"PostgreSQL 連不到：{e}")
    return get_connection


@pytest.fixture
async def pins_pg(pg_conn_factory, gc_pg):
    return gc_pg


# ── schema 三處同步 ───────────────────────────────────


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_migration_schema_and_models_in_sync():
    src = _read("alembic/versions/c067_chat_pins.py")
    assert re.search(r'^revision = "c067"$', src, re.M)
    assert re.search(r'^down_revision = "c066"$', src, re.M)
    upgrade, downgrade = src.split("def downgrade")
    assert "CREATE TABLE IF NOT EXISTS chat_pins" in upgrade
    assert "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS pin_order" in upgrade
    assert "DROP TABLE IF EXISTS chat_pins" in downgrade
    assert "DROP COLUMN IF EXISTS pin_order" in downgrade

    schema = _read("core/database/schema.py")
    assert '("chat_pins", create_chat_pins_table)' in schema
    assert "CREATE TABLE IF NOT EXISTS chat_pins" in schema
    models = _read("core/orm/models.py")
    assert '__tablename__ = "chat_pins"' in models
    assert "pin_order: Mapped[Optional[int]]" in models


def test_pin_order_column_added_in_committed_create_path():
    """reconcile_existing_tables 整批一個交易：reconcile_check_constraints 對已存在的 constraint
    ADD 會失敗（吞掉例外但交易已中止），最後 commit 等於 rollback，整批白做。
    所以 pin_order 也要在 create_all_tables（每步各自 commit）那條路補一次。"""
    from unittest.mock import MagicMock

    from core.database.schema import (
        create_conversation_tables,
        reconcile_existing_tables,
    )

    for fn in (create_conversation_tables, reconcile_existing_tables):
        c = MagicMock()
        fn(c)
        assert any(
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS pin_order" in call.args[0]
            for call in c.execute.call_args_list
        ), fn.__name__


# ── 共用 ──────────────────────────────────────────────


async def _dm(s, a: str, b: str, minutes_ago: int = 0) -> int:
    """a、b 之間的對話＋一則訊息（get_conversations 只列有訊息的對話）"""
    from sqlalchemy import text

    conv_id = (
        await s.execute(
            text(
                "INSERT INTO dm_conversations (user1_id, user2_id, last_message_at) "
                "VALUES (:a, :b, NOW() - make_interval(mins => :m)) RETURNING id"
            ),
            {"a": a, "b": b, "m": minutes_ago},
        )
    ).scalar_one()
    msg_id = (
        await s.execute(
            text(
                "INSERT INTO dm_messages (conversation_id, from_user_id, to_user_id, content) "
                "VALUES (:c, :a, :b, 'hi') RETURNING id"
            ),
            {"c": conv_id, "a": a, "b": b},
        )
    ).scalar_one()
    await s.execute(
        text("UPDATE dm_conversations SET last_message_id = :m WHERE id = :c"),
        {"m": msg_id, "c": conv_id},
    )
    return conv_id


async def _group(s, owner: str, name: str = "投資閒聊") -> int:
    from core.orm.group_chat_repo import group_chat_repo

    result = await group_chat_repo.create_group(owner, name, session=s)
    assert result["success"] is True, result
    return result["group"]["id"]


def _keys(pins):
    return [(p["kind"], p["id"]) for p in pins]


def _positions(pins):
    return [p["position"] for p in pins]


# ── 置頂／取消／清單 ───────────────────────────────────


async def test_pin_unpin_roundtrip(pins_pg):
    from core.orm.chat_pins_repo import chat_pins_repo

    s, u, _ = pins_pg
    conv = await _dm(s, u["a"], u["b"])
    gid = await _group(s, u["a"])

    r1 = await chat_pins_repo.pin(u["a"], "dm", conv, session=s)
    r2 = await chat_pins_repo.pin(u["a"], "group", gid, session=s)
    assert r1["success"] and r2["success"]
    assert r2["pins"] == [
        {"kind": "dm", "id": conv, "position": 0},
        {"kind": "group", "id": gid, "position": 1},
    ]
    assert await chat_pins_repo.list_pins(u["a"], session=s) == r2["pins"]
    # 別人的清單不受影響
    assert await chat_pins_repo.list_pins(u["b"], session=s) == []

    after = await chat_pins_repo.unpin(u["a"], "dm", conv, session=s)
    assert after == [{"kind": "group", "id": gid, "position": 0}], (
        "取消後要重編成 0..n-1"
    )
    # 沒置頂的取消也不算錯
    assert await chat_pins_repo.unpin(u["a"], "dm", conv, session=s) == after


async def test_pin_is_idempotent_and_keeps_position(pins_pg):
    from core.orm.chat_pins_repo import chat_pins_repo

    s, u, _ = pins_pg
    c1 = await _dm(s, u["a"], u["b"])
    c2 = await _dm(s, u["a"], u["c"])
    await chat_pins_repo.pin(u["a"], "dm", c1, session=s)
    await chat_pins_repo.pin(u["a"], "dm", c2, session=s)
    again = await chat_pins_repo.pin(u["a"], "dm", c1, session=s)
    assert again["success"] is True
    assert _keys(again["pins"]) == [("dm", c1), ("dm", c2)], "重按置頂不能被移到最後"
    assert _positions(again["pins"]) == [0, 1]


async def test_pin_limit_counts_dm_and_group_together(pins_pg):
    from core.orm.chat_pins_repo import MAX_PINS, chat_pins_repo

    s, u, make = pins_pg
    gid = await _group(s, u["a"])
    assert (await chat_pins_repo.pin(u["a"], "group", gid, session=s))["success"]
    convs = []
    for i in range(MAX_PINS):
        other = await make.user(f"p{i}")
        convs.append(await _dm(s, u["a"], other))
    for conv in convs[: MAX_PINS - 1]:
        assert (await chat_pins_repo.pin(u["a"], "dm", conv, session=s))["success"]

    over = await chat_pins_repo.pin(u["a"], "dm", convs[-1], session=s)
    assert over == {"success": False, "error": "pin_limit_reached"}
    assert len(await chat_pins_repo.list_pins(u["a"], session=s)) == MAX_PINS
    # 已經置頂的重按不算超過上限
    assert (await chat_pins_repo.pin(u["a"], "group", gid, session=s))["success"]


async def test_pin_requires_access(pins_pg):
    from core.orm.chat_pins_repo import chat_pins_repo

    s, u, _ = pins_pg
    others_dm = await _dm(s, u["b"], u["c"])
    others_group = await _group(s, u["b"])

    for kind, target in (
        ("dm", others_dm),
        ("group", others_group),
        ("dm", 2_000_000_000),
        ("group", 2_000_000_000),
    ):
        result = await chat_pins_repo.pin(u["a"], kind, target, session=s)
        assert result == {"success": False, "error": "not_found"}, (kind, target)
    assert await chat_pins_repo.list_pins(u["a"], session=s) == []
    # kind 寫錯也不會寫進 DB（CHECK 擋之前 repo 先擋）
    bad = await chat_pins_repo.pin(u["a"], "channel", others_dm, session=s)
    assert bad == {"success": False, "error": "invalid_kind"}


async def test_stale_group_pin_is_pruned_and_frees_a_slot(pins_pg):
    """退群（最後一人退出＝刪群）後置頂自動消失、不佔名額、position 重編"""
    from core.orm.chat_pins_repo import MAX_PINS, chat_pins_repo
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = pins_pg
    gid = await _group(s, u["a"])
    await chat_pins_repo.pin(u["a"], "group", gid, session=s)
    convs = []
    for i in range(MAX_PINS):
        other = await make.user(f"q{i}")
        convs.append(await _dm(s, u["a"], other))
    for conv in convs[: MAX_PINS - 1]:
        await chat_pins_repo.pin(u["a"], "dm", conv, session=s)
    assert (await chat_pins_repo.pin(u["a"], "dm", convs[-1], session=s))[
        "error"
    ] == "pin_limit_reached"

    assert (await group_chat_repo.leave_group(gid, u["a"], session=s))["success"]

    pins = await chat_pins_repo.list_pins(u["a"], session=s)
    assert ("group", gid) not in _keys(pins)
    assert _positions(pins) == list(range(MAX_PINS - 1))
    freed = await chat_pins_repo.pin(u["a"], "dm", convs[-1], session=s)
    assert freed["success"] is True, "失效的置頂不能佔名額"
    assert freed["pins"][-1] == {
        "kind": "dm",
        "id": convs[-1],
        "position": MAX_PINS - 1,
    }


async def test_removed_member_pin_is_pruned(pins_pg):
    """被踢（group_members 列不見）也算失效，排序時直接略過"""
    from sqlalchemy import text

    from core.orm.chat_pins_repo import chat_pins_repo

    s, u, _ = pins_pg
    gid = await _group(s, u["b"])
    await s.execute(
        text("INSERT INTO group_members (group_id, user_id) VALUES (:g, :u)"),
        {"g": gid, "u": u["a"]},
    )
    conv = await _dm(s, u["a"], u["c"])
    await chat_pins_repo.pin(u["a"], "group", gid, session=s)
    await chat_pins_repo.pin(u["a"], "dm", conv, session=s)
    await s.execute(
        text("DELETE FROM group_members WHERE group_id = :g AND user_id = :u"),
        {"g": gid, "u": u["a"]},
    )
    pins = await chat_pins_repo.reorder(
        u["a"], [("group", gid), ("dm", conv)], session=s
    )
    assert pins == [{"kind": "dm", "id": conv, "position": 0}]


# ── 私訊列表看不到的對話（封鎖、整段刪除）＝失效置頂 ─────────


async def _hide_all(s, conv_id: int, user_id: str) -> None:
    """同 hide_conversation_for_user：對話每則訊息都記一筆 dm_message_deletions"""
    from sqlalchemy import text

    await s.execute(
        text(
            "INSERT INTO dm_message_deletions (message_id, user_id) "
            "SELECT id, :u FROM dm_messages WHERE conversation_id = :c"
        ),
        {"u": user_id, "c": conv_id},
    )


async def _fill_pins(s, make, owner: str, tag: str):
    """owner 置頂滿 MAX_PINS 個私訊，回傳 (對話 id, 對方 id) 清單"""
    from core.orm.chat_pins_repo import MAX_PINS, chat_pins_repo

    pairs = []
    for i in range(MAX_PINS):
        other = await make.user(f"{tag}{i}")
        conv = await _dm(s, owner, other)
        assert (await chat_pins_repo.pin(owner, "dm", conv, session=s))["success"]
        pairs.append((conv, other))
    return pairs


async def test_blocked_dm_pin_is_pruned_and_frees_a_slot(pins_pg):
    from core.orm.chat_pins_repo import MAX_PINS, chat_pins_repo

    s, u, make = pins_pg
    pairs = await _fill_pins(s, make, u["a"], "bk")
    spare = await _dm(s, u["a"], u["b"])
    assert (await chat_pins_repo.pin(u["a"], "dm", spare, session=s))[
        "error"
    ] == "pin_limit_reached"

    blocked_conv, blocked_user = pairs[3]
    await make.block(u["a"], blocked_user)

    pins = await chat_pins_repo.list_pins(u["a"], session=s)
    assert ("dm", blocked_conv) not in _keys(pins), "封鎖後列表看不到，置頂也要消失"
    assert _positions(pins) == list(range(MAX_PINS - 1))
    freed = await chat_pins_repo.pin(u["a"], "dm", spare, session=s)
    assert freed["success"] is True, "看不到的置頂不能佔名額"
    assert ("dm", blocked_conv) not in _keys(freed["pins"])
    # 封鎖中的對話不能再置頂
    again = await chat_pins_repo.pin(u["a"], "dm", blocked_conv, session=s)
    assert again == {"success": False, "error": "not_found"}


async def test_block_follows_dm_list_rules(pins_pg):
    """只被對方封鎖（對方的列表才看不到）→ 我的置頂留著；互相封鎖且我是後封鎖的那方 → 失效"""
    from sqlalchemy import text

    from core.orm.chat_pins_repo import chat_pins_repo

    s, u, make = pins_pg
    conv = await _dm(s, u["a"], u["b"])
    await chat_pins_repo.pin(u["a"], "dm", conv, session=s)
    await chat_pins_repo.pin(u["b"], "dm", conv, session=s)

    await make.block(u["b"], u["a"])  # b 封鎖 a
    assert _keys(await chat_pins_repo.list_pins(u["a"], session=s)) == [("dm", conv)]
    assert await chat_pins_repo.list_pins(u["b"], session=s) == []

    await s.execute(
        text(
            "UPDATE friendships SET mutual_block = TRUE "
            "WHERE user_id = :b AND friend_id = :a AND status = 'blocked'"
        ),
        {"a": u["a"], "b": u["b"]},
    )  # a 也封鎖了（後封鎖的那方記在 mutual_block）
    assert await chat_pins_repo.list_pins(u["a"], session=s) == []


async def test_all_messages_hidden_dm_pin_is_pruned(pins_pg):
    """刪除對話（整段訊息只對自己隱藏）→ 私訊列表與置頂一起消失；對方不受影響"""
    from core.orm.chat_pins_repo import chat_pins_repo
    from core.orm.messages_repo import messages_repo

    s, u, _ = pins_pg
    conv = await _dm(s, u["a"], u["b"])
    kept = await _dm(s, u["a"], u["c"])
    for owner in (u["a"], u["b"]):
        await chat_pins_repo.pin(owner, "dm", conv, session=s)
    await chat_pins_repo.pin(u["a"], "dm", kept, session=s)

    await _hide_all(s, conv, u["a"])

    assert await chat_pins_repo.list_pins(u["a"], session=s) == [
        {"kind": "dm", "id": kept, "position": 0}
    ]
    listed = [c["id"] for c in await messages_repo.get_conversations(u["a"], session=s)]
    assert conv not in listed and kept in listed, "置頂與私訊列表要用同一個可見條件"
    assert _keys(await chat_pins_repo.list_pins(u["b"], session=s)) == [("dm", conv)]
    assert [
        c["id"] for c in await messages_repo.get_conversations(u["b"], session=s)
    ] == [conv]
    hidden = await chat_pins_repo.pin(u["a"], "dm", conv, session=s)
    assert hidden == {"success": False, "error": "not_found"}

    # 對方再傳一則（沒被隱藏）→ 對話回到列表，可以再置頂
    sent = await messages_repo.send_message(u["b"], u["a"], "還在嗎", session=s)
    assert sent["success"] is True, sent
    listed = [c["id"] for c in await messages_repo.get_conversations(u["a"], session=s)]
    assert conv in listed
    assert (await chat_pins_repo.pin(u["a"], "dm", conv, session=s))["success"]


# ── 排序 ───────────────────────────────────────────────


async def test_reorder_is_lenient(pins_pg):
    from core.orm.chat_pins_repo import chat_pins_repo

    s, u, _ = pins_pg
    x = await _dm(s, u["a"], u["b"])
    y = await _dm(s, u["a"], u["c"])
    z = await _dm(s, u["a"], u["d"])
    not_pinned = await _dm(s, u["a"], u["e"])
    for conv in (x, y, z):
        await chat_pins_repo.pin(u["a"], "dm", conv, session=s)

    pins = await chat_pins_repo.reorder(
        u["a"],
        [("dm", z), ("dm", 2_000_000_000), ("dm", not_pinned), ("dm", x), ("dm", x)],
        session=s,
    )
    assert _keys(pins) == [("dm", z), ("dm", x), ("dm", y)], (
        "列到的照順序在前、沒列到的接在後面、不認得的略過"
    )
    assert _positions(pins) == [0, 1, 2]
    assert await chat_pins_repo.list_pins(u["a"], session=s) == pins
    # 空清單＝不動
    assert await chat_pins_repo.reorder(u["a"], [], session=s) == pins


# ── 列表帶 pin_position ────────────────────────────────


async def test_conversations_put_pinned_first_even_beyond_first_page(pins_pg):
    from core.orm.chat_pins_repo import chat_pins_repo
    from core.orm.messages_repo import messages_repo

    s, u, _ = pins_pg
    newest = await _dm(s, u["a"], u["b"], minutes_ago=1)
    middle = await _dm(s, u["a"], u["c"], minutes_ago=10)
    oldest = await _dm(s, u["a"], u["d"], minutes_ago=600)
    await chat_pins_repo.pin(u["a"], "dm", oldest, session=s)
    await chat_pins_repo.pin(u["a"], "dm", middle, session=s)
    # 對方的置頂不影響我的列表
    await chat_pins_repo.pin(u["b"], "dm", newest, session=s)

    first_page = await messages_repo.get_conversations(u["a"], limit=1, session=s)
    assert [c["id"] for c in first_page] == [oldest], "置頂的要在第一頁，即使最舊"
    assert first_page[0]["pin_position"] == 0

    convs = await messages_repo.get_conversations(u["a"], session=s)
    assert [(c["id"], c["pin_position"]) for c in convs] == [
        (oldest, 0),
        (middle, 1),
        (newest, None),
    ]
    await chat_pins_repo.reorder(u["a"], [("dm", middle)], session=s)
    convs = await messages_repo.get_conversations(u["a"], session=s)
    assert [c["id"] for c in convs] == [middle, oldest, newest]

    theirs = await messages_repo.get_conversations(u["b"], session=s)
    assert [(c["id"], c["pin_position"]) for c in theirs] == [(newest, 0)]


async def test_list_groups_includes_pin_position(pins_pg):
    from core.orm.chat_pins_repo import chat_pins_repo
    from core.orm.group_chat_repo import group_chat_repo

    s, u, _ = pins_pg
    g1 = await _group(s, u["a"], "一群")
    g2 = await _group(s, u["a"], "二群")
    await chat_pins_repo.pin(u["a"], "group", g2, session=s)

    groups = {g["id"]: g for g in await group_chat_repo.list_groups(u["a"], session=s)}
    assert groups[g2]["pin_position"] == 0
    assert groups[g1]["pin_position"] is None


# ── 真的有 commit（自己的連線寫、另一條連線讀） ─────────


def _cleanup_users(get_connection, users, conv_ids=()):
    conn = get_connection()
    try:
        c = conn.cursor()
        for cid in conv_ids:
            c.execute("DELETE FROM dm_messages WHERE conversation_id = %s", (cid,))
            c.execute("DELETE FROM dm_conversations WHERE id = %s", (cid,))
        c.execute("DELETE FROM chat_pins WHERE user_id = ANY(%s)", (list(users),))
        c.execute("DELETE FROM users WHERE user_id = ANY(%s)", (list(users),))
        conn.commit()
    finally:
        conn.close()


async def test_pin_writes_are_committed(pg_conn_factory):
    from core.orm.chat_pins_repo import chat_pins_repo

    suffix = uuid.uuid4().hex[:8]
    a, b = f"t-pin-a-{suffix}", f"t-pin-b-{suffix}"
    conv_ids = []
    conn = pg_conn_factory()
    try:
        c = conn.cursor()
        for uid in (a, b):
            c.execute(
                "INSERT INTO users (user_id, username) VALUES (%s, %s)", (uid, uid)
            )
        c.execute(
            "INSERT INTO dm_conversations (user1_id, user2_id) VALUES (%s, %s) RETURNING id",
            (a, b),
        )
        conv_ids.append(c.fetchone()[0])
        # 私訊列表只列有訊息的對話，置頂也一樣
        c.execute(
            "INSERT INTO dm_messages (conversation_id, from_user_id, to_user_id, content) "
            "VALUES (%s, %s, %s, 'hi')",
            (conv_ids[0], a, b),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        assert (await chat_pins_repo.pin(a, "dm", conv_ids[0]))["success"] is True

        def _read():
            fresh = pg_conn_factory()
            try:
                cur = fresh.cursor()
                cur.execute(
                    "SELECT kind, target_id, position FROM chat_pins WHERE user_id = %s",
                    (a,),
                )
                return cur.fetchall()
            finally:
                fresh.close()

        assert _read() == [("dm", conv_ids[0], 0)], "置頂沒 commit"
        await chat_pins_repo.unpin(a, "dm", conv_ids[0])
        assert _read() == [], "取消置頂沒 commit"
    finally:
        _cleanup_users(pg_conn_factory, (a, b), conv_ids)


# ── AI 對話 sessions.pin_order ─────────────────────────


@pytest.fixture
def session_rows(pg_conn_factory):
    """兩個使用者各幾個對話（已 commit），結束清掉。updated_at 越後面越舊。"""
    from core.database.chat import create_session

    suffix = uuid.uuid4().hex[:8]
    me, other = f"t-spin-me-{suffix}", f"t-spin-ot-{suffix}"
    mine = [f"{me}-s{i}" for i in range(4)]
    theirs = [f"{other}-s{i}" for i in range(2)]
    conn = pg_conn_factory()
    try:
        c = conn.cursor()
        for uid in (me, other):  # sessions.user_id 有 FK（reconcile_foreign_keys）
            c.execute(
                "INSERT INTO users (user_id, username) VALUES (%s, %s)", (uid, uid)
            )
        conn.commit()
    finally:
        conn.close()
    for uid, sids in ((me, mine), (other, theirs)):
        for sid in sids:
            create_session(sid, title=sid, user_id=uid)
    conn = pg_conn_factory()
    try:
        c = conn.cursor()
        for i, sid in enumerate(mine + theirs):
            c.execute(
                "UPDATE sessions SET updated_at = NOW() - make_interval(mins => %s) "
                "WHERE session_id = %s",
                (i, sid),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        yield me, mine, other, theirs
    finally:
        conn = pg_conn_factory()
        try:
            c = conn.cursor()
            c.execute(
                "DELETE FROM sessions WHERE session_id = ANY(%s)", (mine + theirs,)
            )
            c.execute("DELETE FROM users WHERE user_id IN (%s, %s)", (me, other))
            conn.commit()
        finally:
            conn.close()


def _pin_state(get_connection, sids):
    """另一條連線讀 {session_id: (is_pinned, pin_order)}"""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT session_id, is_pinned, pin_order FROM sessions WHERE session_id = ANY(%s)",
            (list(sids),),
        )
        return {sid: (pinned, order) for sid, pinned, order in c.fetchall()}
    finally:
        conn.close()


def test_session_pin_order_assignment_and_listing(pg_conn_factory, session_rows):
    from core.database.chat import get_sessions, toggle_session_pin

    me, (s0, s1, s2, s3), _, _ = session_rows
    toggle_session_pin(s2, True)
    toggle_session_pin(s0, True)
    toggle_session_pin(s2, True)  # 重按：保留原順序
    state = _pin_state(pg_conn_factory, [s0, s2])
    assert state == {s2: (1, 0), s0: (1, 1)}

    rows = get_sessions(user_id=me)
    assert [r["id"] for r in rows] == [s2, s0, s1, s3], (
        "置頂照 pin_order，其他照 updated_at"
    )
    assert [r["pin_order"] for r in rows] == [0, 1, None, None]
    assert [r["is_pinned"] for r in rows] == [True, True, False, False]

    toggle_session_pin(s2, False)
    assert _pin_state(pg_conn_factory, [s2]) == {s2: (0, None)}
    toggle_session_pin(s3, True)
    toggle_session_pin(s2, True)
    assert _pin_state(pg_conn_factory, [s0, s2, s3]) == {
        s0: (1, 1),
        s3: (1, 2),
        s2: (1, 3),
    }, "新置頂接在最後面（最大值 + 1）"


def test_legacy_pinned_without_order_sort_after_ordered(pg_conn_factory, session_rows):
    """c067 之前置頂的 pin_order 是 NULL：排在有編號的後面，彼此照 updated_at"""
    from core.database.chat import get_sessions, toggle_session_pin

    me, (s0, s1, s2, s3), _, _ = session_rows
    conn = pg_conn_factory()
    try:
        conn.cursor().execute(
            "UPDATE sessions SET is_pinned = 1, pin_order = NULL WHERE session_id = ANY(%s)",
            ([s0, s1],),
        )
        conn.commit()
    finally:
        conn.close()
    toggle_session_pin(s3, True)
    assert [r["id"] for r in get_sessions(user_id=me)] == [s3, s0, s1, s2]


def test_reorder_pinned_sessions_is_lenient_and_owner_only(
    pg_conn_factory, session_rows
):
    from core.database.chat import (
        get_sessions,
        reorder_pinned_sessions,
        toggle_session_pin,
    )

    me, (s0, s1, s2, s3), other, (o0, o1) = session_rows
    for sid in (s0, s1, s2, o0):
        toggle_session_pin(sid, True)
    before_other = _pin_state(pg_conn_factory, [o0, o1])

    reorder_pinned_sessions(me, [s2, o0, o1, s3, "no-such-session", s0, s2])

    assert _pin_state(pg_conn_factory, [s0, s1, s2, s3]) == {
        s2: (1, 0),
        s0: (1, 1),
        s1: (1, 2),
        s3: (0, None),
    }, "列到的在前、沒列到的置頂接後面、沒置頂的不動"
    assert _pin_state(pg_conn_factory, [o0, o1]) == before_other, "別人的對話不能被動到"
    assert [r["id"] for r in get_sessions(user_id=me)][:3] == [s2, s0, s1]
    # 拿別人的 id 來排，自己的置頂順序維持
    reorder_pinned_sessions(other, [s1, s0, s2])
    assert _pin_state(pg_conn_factory, [s0, s1, s2]) == {
        s2: (1, 0),
        s0: (1, 1),
        s1: (1, 2),
    }


# ── 路由 ───────────────────────────────────────────────


def test_limiter_decorator_is_below_router_decorator():
    """@limiter 在 @router 上面時 router 登記的是沒包 limiter 的函式，限流等於沒有"""
    lines = (REPO / "api/routers/chat_pins.py").read_text(encoding="utf-8").splitlines()
    routes = [
        i
        for i, line in enumerate(lines)
        if re.match(r"@router\.(get|put|delete)\(", line)
    ]
    assert len(routes) == 4
    for i in routes:
        assert lines[i + 1].startswith("@limiter.limit"), f"第 {i + 2} 行要是 @limiter"
    analysis = (
        (REPO / "api/routers/analysis.py").read_text(encoding="utf-8").splitlines()
    )
    i = analysis.index('@router.put("/api/chat/sessions/pin-order")')
    assert analysis[i + 1].startswith("@limiter.limit")


@pytest.fixture
def fake_pins(monkeypatch):
    from api.routers import chat_pins as router

    calls = []
    pins = [{"kind": "dm", "id": 5, "position": 0}]

    async def list_pins(user_id):
        calls.append(("list", user_id))
        return pins

    async def pin(user_id, kind, target_id):
        calls.append(("pin", user_id, kind, target_id))
        if target_id == 404:
            return {"success": False, "error": "not_found"}
        if target_id == 400:
            return {"success": False, "error": "pin_limit_reached"}
        return {"success": True, "pins": pins}

    async def unpin(user_id, kind, target_id):
        calls.append(("unpin", user_id, kind, target_id))
        return []

    async def reorder(user_id, items):
        calls.append(("reorder", user_id, items))
        return pins

    for name, fn in (
        ("list_pins", list_pins),
        ("pin", pin),
        ("unpin", unpin),
        ("reorder", reorder),
    ):
        monkeypatch.setattr(router.chat_pins_repo, name, fn)
    return calls


async def test_chat_pins_routes(client, auth_headers, fake_pins):
    expected = {"success": True, "pins": [{"kind": "dm", "id": 5, "position": 0}]}

    r = await client.get("/api/chat-pins", headers=auth_headers)
    assert r.status_code == 200 and r.json() == expected

    r = await client.put("/api/chat-pins/group/7", headers=auth_headers)
    assert r.status_code == 200 and r.json() == expected

    r = await client.put(
        "/api/chat-pins/order",
        headers=auth_headers,
        json={"items": [{"kind": "group", "id": 7}, {"kind": "dm", "id": 5}]},
    )
    assert r.status_code == 200 and r.json() == expected

    r = await client.delete("/api/chat-pins/dm/5", headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"success": True, "pins": []}

    user = "test-user-001"
    assert fake_pins == [
        ("list", user),
        ("pin", user, "group", 7),
        ("reorder", user, [("group", 7), ("dm", 5)]),
        ("unpin", user, "dm", 5),
    ], "order 不能被當成 {kind}/{target_id}"


async def test_chat_pins_errors(client, auth_headers, fake_pins):
    r = await client.put("/api/chat-pins/dm/404", headers=auth_headers)
    assert r.status_code == 404 and r.json()["detail"] == "not_found"
    r = await client.put("/api/chat-pins/dm/400", headers=auth_headers)
    assert r.status_code == 400 and r.json()["detail"] == "pin_limit_reached"

    for bad in (
        "/api/chat-pins/channel/5",
        "/api/chat-pins/dm/0",
        "/api/chat-pins/dm/x",
    ):
        r = await client.put(bad, headers=auth_headers)
        assert r.status_code == 422, bad
    r = await client.put(
        "/api/chat-pins/order",
        headers=auth_headers,
        json={"items": [{"kind": "dm", "id": i} for i in range(1, 22)]},
    )
    assert r.status_code == 422, "最多 20 項"
    r = await client.put(
        "/api/chat-pins/order",
        headers=auth_headers,
        json={"items": [{"kind": "channel", "id": 1}]},
    )
    assert r.status_code == 422
    assert [c[0] for c in fake_pins] == ["pin", "pin"], "驗證沒過不能碰到 repo"


async def test_session_pin_order_route(client, auth_headers, monkeypatch):
    """/api/chat/sessions/pin-order 不能被 /{session_id} 系列吃掉；user_id 只從登入身分來"""
    from api.routers import analysis

    calls = []

    async def fake_run_sync(fn, *args):
        calls.append((fn.__name__, args))

    monkeypatch.setattr(analysis, "run_sync", fake_run_sync)
    r = await client.put(
        "/api/chat/sessions/pin-order",
        headers=auth_headers,
        json={"session_ids": ["s2", "s1"]},
    )
    assert r.status_code == 200 and r.json() == {"success": True}
    assert calls == [("reorder_pinned_sessions", ("test-user-001", ["s2", "s1"]))]

    r = await client.put(
        "/api/chat/sessions/pin-order",
        headers=auth_headers,
        json={"session_ids": [f"s{i}" for i in range(101)]},
    )
    assert r.status_code == 422, "最多 100 個"
    assert len(calls) == 1
