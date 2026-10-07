"""對話自訂順序（c068）：chat_order_repo、GET /api/messages/conversations?order=、PUT／DELETE /api/chat-order。

- repo 用真 PostgreSQL、整段交易最後 rollback（tests/group_chat_pg）；pg_conn_factory 讓測試庫
  跑一次 init_db（schema.py 建 chat_order）。
- 「真的有 commit」那題自己開連線寫入、另一條連線讀回、跑完清掉。
- 路由把 repo 換成假的，走真的 app 路由。
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
    from core.database.connection import get_connection

    try:
        get_connection().close()
    except Exception as e:  # noqa: BLE001 — 連不到就 skip，不是測試失敗
        pytest.skip(f"PostgreSQL 連不到：{e}")
    return get_connection


@pytest.fixture
async def opg(pg_conn_factory, gc_pg):
    return gc_pg


# ── 共用 ──────────────────────────────────────────────


async def _dm(s, a: str, b: str, minutes_ago: int = 0) -> int:
    """a、b 之間的對話＋一則訊息（私訊列表只列有訊息的對話）。
    同一個交易裡 NOW() 不變：minutes_ago 一樣的對話最後動態完全相同"""
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


async def _group(s, owner: str, name: str = "投資閒聊", minutes_ago: int = 0) -> int:
    from sqlalchemy import text

    from core.orm.group_chat_repo import group_chat_repo

    result = await group_chat_repo.create_group(owner, name, session=s)
    assert result["success"] is True, result
    gid = result["group"]["id"]
    await s.execute(
        text(
            "UPDATE group_chats SET last_message_at = NOW() - make_interval(mins => :m) "
            "WHERE id = :g"
        ),
        {"m": minutes_ago, "g": gid},
    )
    return gid


async def _set_positions(s, user_id: str, keys) -> None:
    from sqlalchemy import text

    for i, (kind, target_id) in enumerate(keys):
        await s.execute(
            text(
                "INSERT INTO chat_order (user_id, kind, target_id, position) "
                "VALUES (:u, :k, :t, :p)"
            ),
            {"u": user_id, "k": kind, "t": target_id, "p": i},
        )


async def _stored(s, user_id: str):
    from sqlalchemy import text

    rows = await s.execute(
        text(
            "SELECT kind, target_id FROM chat_order WHERE user_id = :u ORDER BY position"
        ),
        {"u": user_id},
    )
    return [tuple(r) for r in rows]


async def _convs(s, user_id: str, order: str = "custom", **kw):
    from core.orm.messages_repo import messages_repo

    return [
        c["id"]
        for c in await messages_repo.get_conversations(
            user_id, order=order, session=s, **kw
        )
    ]


def _keys(order):
    return [(o["kind"], o["id"]) for o in order]


# ── schema ────────────────────────────────────────────


def test_schema_has_chat_order():
    schema = (REPO / "core/database/schema.py").read_text(encoding="utf-8")
    assert '("chat_order", create_chat_order_table)' in schema
    assert re.search(r"CREATE TABLE IF NOT EXISTS chat_order", schema)
    models = (REPO / "core/orm/models.py").read_text(encoding="utf-8")
    assert '__tablename__ = "chat_order"' in models


# ── 列表排序 ───────────────────────────────────────────


async def test_recent_mode_unchanged_and_custom_mode_order(opg):
    from core.orm.chat_pins_repo import chat_pins_repo
    from core.orm.messages_repo import messages_repo

    s, u, make = opg
    x = await _dm(s, u["a"], u["b"], minutes_ago=1)
    y = await _dm(s, u["a"], u["c"], minutes_ago=10)
    z = await _dm(s, u["a"], u["d"], minutes_ago=100)
    w = await _dm(s, u["a"], u["e"], minutes_ago=1000)
    p = await _dm(s, u["a"], u["f"], minutes_ago=5000)
    assert (await chat_pins_repo.pin(u["a"], "dm", p, session=s))["success"]
    # 自訂位置：z 在 x 前面；p 置頂中也有位置（置頂優先，位置被忽略）；y、w 還沒排過
    await _set_positions(s, u["a"], [("dm", p), ("dm", z), ("dm", x)])

    assert await _convs(s, u["a"], "recent") == [p, x, y, z, w], (
        "recent 照舊：置頂＋最後動態"
    )
    assert await _convs(s, u["a"]) == [p, y, w, z, x], (
        "custom：置頂 → 還沒排過的（照最後動態）→ 自訂位置"
    )
    rows = await messages_repo.get_conversations(u["a"], session=s)
    assert {c["id"]: c["order_position"] for c in rows} == {
        p: 0,
        x: 2,
        y: None,
        z: 1,
        w: None,
    }
    assert [c["id"] for c in rows] == [p, x, y, z, w], "預設是 recent"


async def test_custom_mode_pagination_is_consistent(opg):
    s, u, make = opg
    others = [await make.user(f"pg{i}") for i in range(9)]
    # 同一分鐘的一大串（最後動態完全相同），測排序是全序
    convs = [await _dm(s, u["a"], o, minutes_ago=5) for o in others[:6]]
    convs += [await _dm(s, u["a"], o, minutes_ago=i) for i, o in enumerate(others[6:])]
    await _set_positions(s, u["a"], [("dm", convs[0]), ("dm", convs[7])])

    full = await _convs(s, u["a"])
    c = convs
    assert full == [c[6], c[8], c[5], c[4], c[3], c[2], c[1], c[0], c[7]], (
        "沒排過的照最後動態、同時間的新 id 在前（全序），有位置的排在後面"
    )
    paged = []
    for offset in range(0, len(convs), 2):
        paged += await _convs(s, u["a"], limit=2, offset=offset)
    assert paged == full, "分頁要照同一個順序切，不能重複或漏掉"


async def test_list_groups_includes_order_position(opg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, _ = opg
    g1 = await _group(s, u["a"], "一群")
    g2 = await _group(s, u["a"], "二群")
    await _set_positions(s, u["a"], [("group", g2)])
    groups = {g["id"]: g for g in await group_chat_repo.list_groups(u["a"], session=s)}
    assert groups[g2]["order_position"] == 0
    assert groups[g1]["order_position"] is None


# ── 排序（PUT） ────────────────────────────────────────


async def test_first_reorder_snapshots_everything_unloaded_after(opg):
    """第一次排：看得到的全部照最後動態編位置，前端只送已載入那段 → 沒載入的接在後面"""
    from core.orm.chat_order_repo import chat_order_repo

    s, u, make = opg
    d1 = await _dm(s, u["a"], u["b"], minutes_ago=1)
    g = await _group(s, u["a"], minutes_ago=5)
    d2 = await _dm(s, u["a"], u["c"], minutes_ago=10)
    d3 = await _dm(s, u["a"], u["d"], minutes_ago=100)
    d4 = await _dm(s, u["a"], u["e"], minutes_ago=1000)

    order = await chat_order_repo.reorder(
        u["a"], [("dm", d2), ("dm", d1), ("group", g)], session=s
    )

    expected = [("dm", d2), ("dm", d1), ("group", g), ("dm", d3), ("dm", d4)]
    assert _keys(order) == expected
    assert [o["position"] for o in order] == list(range(5))
    assert await _stored(s, u["a"]) == expected
    assert await _convs(s, u["a"]) == [d2, d1, d3, d4]


async def test_subset_reorder_keeps_other_slots(opg):
    from core.orm.chat_order_repo import chat_order_repo

    s, u, _ = opg
    a, b, c, d, e = [
        await _dm(s, u["a"], u[k], minutes_ago=i) for i, k in enumerate("bcdef")
    ]
    await _set_positions(s, u["a"], [("dm", k) for k in (a, b, c, d, e)])

    order = await chat_order_repo.reorder(
        u["a"], [("dm", d), ("dm", b), ("dm", d)], session=s
    )
    assert _keys(order) == [("dm", k) for k in (a, d, c, b, e)], (
        "列到的放進它們原本佔的格子，其他不動；重複的只算一次"
    )
    # 空清單＝只補位置、不動順序
    again = await chat_order_repo.reorder(u["a"], [], session=s)
    assert again == order


async def test_new_chat_after_snapshot_goes_to_top(opg):
    """排過之後才出現的對話（新對話、對方再傳訊息讓刪掉的對話回來）在非置頂區最上面，
    就算它的最後動態比較舊；下一次排序時固定在最上面那格"""
    from core.orm.chat_order_repo import chat_order_repo

    s, u, make = opg
    a = await _dm(s, u["a"], u["b"], minutes_ago=1)
    b = await _dm(s, u["a"], u["c"], minutes_ago=2)
    await chat_order_repo.reorder(u["a"], [("dm", b), ("dm", a)], session=s)

    newer = await _dm(s, u["a"], u["d"], minutes_ago=0)
    older = await _dm(s, u["a"], u["e"], minutes_ago=600)
    gid = await _group(s, u["a"], minutes_ago=300)
    assert await _convs(s, u["a"]) == [newer, older, b, a]
    assert await _convs(s, u["a"], "recent") == [newer, a, b, older]

    order = await chat_order_repo.reorder(u["a"], [("dm", a), ("dm", b)], session=s)
    assert _keys(order) == [
        ("dm", newer),
        ("group", gid),
        ("dm", older),
        ("dm", a),
        ("dm", b),
    ]


async def test_reorder_ignores_inaccessible_and_other_users(opg):
    """IDOR：別人的對話、別人的群、不存在的 id 一律略過，也不能動到別人的順序"""
    from core.orm.chat_order_repo import chat_order_repo

    s, u, _ = opg
    mine1 = await _dm(s, u["a"], u["b"], minutes_ago=1)
    mine2 = await _dm(s, u["a"], u["c"], minutes_ago=2)
    theirs = await _dm(s, u["b"], u["c"], minutes_ago=3)
    their_group = await _group(s, u["b"])
    await _set_positions(s, u["b"], [("group", their_group), ("dm", theirs)])

    order = await chat_order_repo.reorder(
        u["a"],
        [
            ("dm", theirs),
            ("group", their_group),
            ("dm", 2_000_000_000),
            ("group", 2_000_000_000),
            ("dm", mine2),
            ("dm", mine1),
        ],
        session=s,
    )
    assert _keys(order) == [("dm", mine2), ("dm", mine1)]
    assert await _stored(s, u["a"]) == [("dm", mine2), ("dm", mine1)]
    assert await _stored(s, u["b"]) == [("group", their_group), ("dm", theirs)], (
        "別人的順序不能被動到"
    )


async def test_pinned_items_are_not_auto_placed_but_keep_existing_slot(opg):
    from core.orm.chat_order_repo import chat_order_repo
    from core.orm.chat_pins_repo import chat_pins_repo

    s, u, _ = opg
    a = await _dm(s, u["a"], u["b"], minutes_ago=1)
    b = await _dm(s, u["a"], u["c"], minutes_ago=2)
    c = await _dm(s, u["a"], u["d"], minutes_ago=3)
    await chat_pins_repo.pin(u["a"], "dm", a, session=s)

    order = await chat_order_repo.reorder(u["a"], [("dm", a), ("dm", c)], session=s)
    assert _keys(order) == [("dm", b), ("dm", c)], "置頂的不補位置，列了也略過"

    # 先排好、之後才置頂的：位置留著（取消置頂回到那格），拖曳其他的不影響它
    await chat_pins_repo.unpin(u["a"], "dm", a, session=s)
    await chat_order_repo.reorder(u["a"], [("dm", c), ("dm", b)], session=s)
    assert await _stored(s, u["a"]) == [("dm", a), ("dm", c), ("dm", b)]
    await chat_pins_repo.pin(u["a"], "dm", a, session=s)
    await chat_order_repo.reorder(u["a"], [("dm", b), ("dm", c)], session=s)
    assert await _stored(s, u["a"]) == [("dm", a), ("dm", b), ("dm", c)]
    assert await _convs(s, u["a"]) == [a, b, c]


async def test_stale_rows_are_pruned(opg):
    """退群、群組解散、封鎖、整段刪除 → 位置列清掉、重編 0..n-1"""
    from sqlalchemy import text

    from core.orm.chat_order_repo import chat_order_repo
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = opg
    kept = await _dm(s, u["a"], u["b"], minutes_ago=1)
    blocked = await _dm(s, u["a"], u["c"], minutes_ago=2)
    hidden = await _dm(s, u["a"], u["d"], minutes_ago=3)
    left = await _group(s, u["a"], "退出群", minutes_ago=4)
    dissolved = await _group(s, u["a"], "解散群", minutes_ago=5)
    await chat_order_repo.reorder(u["a"], [], session=s)
    assert len(await _stored(s, u["a"])) == 5

    await make.block(u["a"], u["c"])
    await s.execute(
        text(
            "INSERT INTO dm_message_deletions (message_id, user_id) "
            "SELECT id, :u FROM dm_messages WHERE conversation_id = :c"
        ),
        {"u": u["a"], "c": hidden},
    )
    assert (await group_chat_repo.leave_group(left, u["a"], session=s))["success"]
    assert (await group_chat_repo.dissolve_group(dissolved, u["a"], session=s))[
        "success"
    ]

    order = await chat_order_repo.reorder(
        u["a"], [("dm", blocked), ("dm", hidden), ("dm", kept)], session=s
    )
    assert order == [{"kind": "dm", "id": kept, "position": 0}]
    assert await _stored(s, u["a"]) == [("dm", kept)]


async def test_clear_resets_to_recency(opg):
    from core.orm.chat_order_repo import chat_order_repo

    s, u, _ = opg
    a = await _dm(s, u["a"], u["b"], minutes_ago=1)
    b = await _dm(s, u["a"], u["c"], minutes_ago=2)
    await chat_order_repo.reorder(u["a"], [("dm", b), ("dm", a)], session=s)
    await _set_positions(s, u["b"], [("dm", a)])
    assert await _convs(s, u["a"]) == [b, a]

    await chat_order_repo.clear(u["a"], session=s)
    assert await _stored(s, u["a"]) == []
    assert await _convs(s, u["a"]) == [a, b]
    assert await _stored(s, u["b"]) == [("dm", a)], "只清自己的"


# ── 真的有 commit（自己的連線寫、另一條連線讀） ─────────


async def test_reorder_and_clear_are_committed(pg_conn_factory):
    from core.orm.chat_order_repo import chat_order_repo

    suffix = uuid.uuid4().hex[:8]
    a, b = f"t-ord-a-{suffix}", f"t-ord-b-{suffix}"
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
        conv_id = c.fetchone()[0]
        c.execute(
            "INSERT INTO dm_messages (conversation_id, from_user_id, to_user_id, content) "
            "VALUES (%s, %s, %s, 'hi')",
            (conv_id, a, b),
        )
        conn.commit()
    finally:
        conn.close()

    def _read():
        fresh = pg_conn_factory()
        try:
            cur = fresh.cursor()
            cur.execute(
                "SELECT kind, target_id, position FROM chat_order WHERE user_id = %s",
                (a,),
            )
            return cur.fetchall()
        finally:
            fresh.close()

    try:
        await chat_order_repo.reorder(a, [("dm", conv_id)])
        assert _read() == [("dm", conv_id, 0)], "排序沒 commit"
        await chat_order_repo.clear(a)
        assert _read() == [], "重設沒 commit"
    finally:
        conn = pg_conn_factory()
        try:
            c = conn.cursor()
            c.execute("DELETE FROM chat_order WHERE user_id = ANY(%s)", ([a, b],))
            c.execute("DELETE FROM dm_messages WHERE conversation_id = %s", (conv_id,))
            c.execute("DELETE FROM dm_conversations WHERE id = %s", (conv_id,))
            c.execute("DELETE FROM users WHERE user_id = ANY(%s)", ([a, b],))
            conn.commit()
        finally:
            conn.close()


# ── 路由 ───────────────────────────────────────────────


def test_limiter_decorator_is_below_router_decorator():
    """@limiter 在 @router 上面時 router 登記的是沒包 limiter 的函式，限流等於沒有"""
    lines = (
        (REPO / "api/routers/chat_order.py").read_text(encoding="utf-8").splitlines()
    )
    routes = [
        i
        for i, line in enumerate(lines)
        if re.match(r"@router\.(get|put|delete)\(", line)
    ]
    assert len(routes) == 2
    for i in routes:
        assert lines[i + 1].startswith("@limiter.limit"), f"第 {i + 2} 行要是 @limiter"


@pytest.fixture
def fake_order(monkeypatch):
    from api.routers import chat_order as router

    calls = []
    order = [{"kind": "group", "id": 7, "position": 0}]

    async def reorder(user_id, items):
        calls.append(("reorder", user_id, items))
        return order

    async def clear(user_id):
        calls.append(("clear", user_id))

    monkeypatch.setattr(router.chat_order_repo, "reorder", reorder)
    monkeypatch.setattr(router.chat_order_repo, "clear", clear)
    return calls


async def test_chat_order_routes(client, auth_headers, fake_order):
    r = await client.put(
        "/api/chat-order",
        headers=auth_headers,
        json={"items": [{"kind": "group", "id": 7}, {"kind": "dm", "id": 5}]},
    )
    assert r.status_code == 200
    assert r.json() == {
        "success": True,
        "order": [{"kind": "group", "id": 7, "position": 0}],
    }
    r = await client.delete("/api/chat-order", headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"success": True}

    user = "test-user-001"
    assert fake_order == [
        ("reorder", user, [("group", 7), ("dm", 5)]),
        ("clear", user),
    ], "user_id 只從登入身分來"


async def test_chat_order_validation(client, auth_headers, fake_order):
    for body in (
        {"items": [{"kind": "dm", "id": i} for i in range(1, 202)]},
        {"items": [{"kind": "channel", "id": 1}]},
        {"items": [{"kind": "dm", "id": 0}]},
        {"items": [{"kind": "dm", "id": 2_147_483_648}]},
        {},
    ):
        r = await client.put("/api/chat-order", headers=auth_headers, json=body)
        assert r.status_code == 422, body
    ok = await client.put(
        "/api/chat-order",
        headers=auth_headers,
        json={"items": [{"kind": "dm", "id": i} for i in range(1, 201)]},
    )
    assert ok.status_code == 200, "最多 200 項"
    assert len(fake_order) == 1, "驗證沒過不能碰到 repo"


async def test_conversations_route_passes_order(client, auth_headers, monkeypatch):
    from api.routers import messages as router

    calls = []

    async def get_conversations(user_id, limit=50, offset=0, order="recent"):
        calls.append((user_id, limit, offset, order))
        return []

    async def get_unread_count(user_id):
        return 0

    monkeypatch.setattr(router.messages_repo, "get_conversations", get_conversations)
    monkeypatch.setattr(router.messages_repo, "get_unread_count", get_unread_count)

    r = await client.get(
        "/api/messages/conversations?limit=20&offset=40&order=custom",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    r = await client.get("/api/messages/conversations", headers=auth_headers)
    assert r.status_code == 200
    r = await client.get(
        "/api/messages/conversations?order=bogus", headers=auth_headers
    )
    assert r.status_code == 422
    assert calls == [
        ("test-user-001", 20, 40, "custom"),
        ("test-user-001", 50, 0, "recent"),
    ]
