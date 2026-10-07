"""私訊表情回應（2026-09-29）。

8 個自繪 SVG 表情，DB 只存 key。每人每則一個（按別的＝替換、按同一個＝取消）；
只有對話雙方能按；已收回的不能按，收回時一併清掉。讀取／送出都帶 reactions。
真 PostgreSQL，交易內跑完 rollback，連不到就 skip。
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
async def pg_session():
    from dotenv import load_dotenv
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from core.orm.session import _normalize_pg_url

    load_dotenv()
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        pytest.skip("沒有 PostgreSQL DATABASE_URL")
    engine = create_async_engine(_normalize_pg_url(url))
    try:
        conn = await engine.connect()
    except Exception as e:  # noqa: BLE001 — 連不到就 skip，不是測試失敗
        await engine.dispose()
        pytest.skip(f"PostgreSQL 連不到：{e}")
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    users = {k: f"t-rx-{k}-{suffix}" for k in ("a", "b", "x")}
    for uid in users.values():
        await session.execute(
            text("INSERT INTO users (user_id, username) VALUES (:u, :u)"), {"u": uid}
        )
    try:
        yield session, users
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _msg(s, frm, to, content="hi"):
    from core.orm.messages_repo import messages_repo

    return (await messages_repo.send_message(frm, to, content, session=s))["message"]


async def test_set_replace_and_remove_reaction(pg_session):
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    msg = await _msg(s, u["a"], u["b"])

    first = await messages_repo.set_reaction(msg["id"], u["b"], "love", session=s)
    assert first["success"] is True
    assert first["reactions"] == [{"user_id": u["b"], "reaction": "love"}]
    assert first["conversation_id"] == msg["conversation_id"]
    assert set(first["participants"]) == {u["a"], u["b"]}

    await messages_repo.set_reaction(msg["id"], u["a"], "rocket", session=s)
    replaced = await messages_repo.set_reaction(msg["id"], u["b"], "haha", session=s)
    assert sorted(
        (r["user_id"], r["reaction"]) for r in replaced["reactions"]
    ) == sorted([(u["a"], "rocket"), (u["b"], "haha")]), (
        "每人一個：按別的是替換，不是再加一個"
    )

    removed = await messages_repo.remove_reaction(msg["id"], u["b"], session=s)
    assert removed["reactions"] == [{"user_id": u["a"], "reaction": "rocket"}]

    page = await messages_repo.get_messages(msg["conversation_id"], u["b"], session=s)
    assert page["messages"][-1]["reactions"] == [
        {"user_id": u["a"], "reaction": "rocket"}
    ]


async def test_reaction_rules(pg_session):
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    msg = await _msg(s, u["a"], u["b"])

    assert (await messages_repo.set_reaction(msg["id"], u["x"], "love", session=s))[
        "error"
    ] == "message_not_found"
    assert (await messages_repo.set_reaction(10**9, u["a"], "love", session=s))[
        "error"
    ] == "message_not_found"
    assert (await messages_repo.set_reaction(msg["id"], u["b"], "poop", session=s))[
        "error"
    ] == "invalid_reaction"

    # 被封鎖（任一方向）就不能按：不然被封鎖的人還能推即時事件給對方
    from sqlalchemy import text

    await s.execute(
        text(
            "INSERT INTO friendships (user_id, friend_id, status) VALUES (:a, :b, 'blocked')"
        ),
        {"a": u["a"], "b": u["b"]},
    )
    assert (await messages_repo.set_reaction(msg["id"], u["b"], "love", session=s))[
        "error"
    ] == "blocked"
    await s.execute(
        text("DELETE FROM friendships WHERE user_id = :a AND friend_id = :b"),
        {"a": u["a"], "b": u["b"]},
    )

    await messages_repo.set_reaction(msg["id"], u["b"], "love", session=s)
    await messages_repo.recall_message(msg["id"], u["a"], session=s)
    page = await messages_repo.get_messages(msg["conversation_id"], u["b"], session=s)
    assert page["messages"][-1]["reactions"] == [], "收回時表情一起清掉"
    assert (await messages_repo.set_reaction(msg["id"], u["b"], "ok", session=s))[
        "error"
    ] == "message_recalled"


async def test_send_response_has_empty_reactions(pg_session):
    s, u = pg_session
    msg = await _msg(s, u["a"], u["b"])
    assert msg["reactions"] == []


def test_reaction_request_only_accepts_known_keys():
    from pydantic import ValidationError

    from api.routers.messages import ReactionRequest

    for key in ("like", "love", "haha", "wow", "sad", "rocket", "diamond", "ok"):
        assert ReactionRequest(reaction=key).reaction == key
    with pytest.raises(ValidationError):
        ReactionRequest(reaction="<script>")


async def test_reaction_endpoint_pushes_to_both(monkeypatch):
    from starlette.requests import Request

    import api.routers.messages as router

    pushed = []

    async def fake_set(message_id, user_id, reaction):
        return {
            "success": True,
            "conversation_id": 9,
            "participants": ["a", "b"],
            "reactions": [{"user_id": "a", "reaction": reaction}],
        }

    async def fake_send(user_id, payload):
        pushed.append((user_id, payload))

    monkeypatch.setattr(router.messages_repo, "set_reaction", fake_set)
    monkeypatch.setattr(router.message_manager, "send_to_user", fake_send)
    req = Request(
        {
            "type": "http",
            "method": "PUT",
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )

    body = await router.set_reaction_endpoint.__wrapped__(
        req, 5, router.ReactionRequest(reaction="love"), {"user_id": "a"}
    )

    event = {
        "type": "reaction_updated",
        "message_id": 5,
        "conversation_id": 9,
        "reactions": [{"user_id": "a", "reaction": "love"}],
    }
    assert body == {"success": True, "reactions": event["reactions"]}
    assert ("a", event) in pushed and ("b", event) in pushed


# /with/（第一頁）走舊 psycopg2 路徑，要 commit 才讀得到，結束時清掉
async def test_first_page_raw_path_also_has_reactions():
    from core.database.connection import get_connection
    from core.database.messages.helpers import get_conversation_with_messages
    from core.orm.messages_repo import messages_repo

    try:
        conn = get_connection()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 連不到：{e}")
    suffix = uuid.uuid4().hex[:8]
    a, b = f"t-rxw-a-{suffix}", f"t-rxw-b-{suffix}"
    try:
        c = conn.cursor()
        for uid in (a, b):
            c.execute(
                "INSERT INTO users (user_id, username) VALUES (%s, %s)", (uid, uid)
            )
        conn.commit()
        msg = (await messages_repo.send_message(a, b, "第一頁"))["message"]
        await messages_repo.set_reaction(msg["id"], b, "diamond")

        page = get_conversation_with_messages(a, b)

        assert page["messages"][-1]["reactions"] == [
            {"user_id": b, "reaction": "diamond"}
        ]
    finally:
        c = conn.cursor()
        c.execute("DELETE FROM dm_messages WHERE from_user_id IN (%s, %s)", (a, b))
        c.execute("DELETE FROM dm_conversations WHERE user1_id IN (%s, %s)", (a, b))
        c.execute("DELETE FROM users WHERE user_id IN (%s, %s)", (a, b))
        conn.commit()
        conn.close()


# review 2026-09-29：收回一個本來就沒按的表情不要推 WS（不然連打 DELETE 能洗對方的畫面）
async def test_remove_reaction_endpoint_pushes_only_when_changed(monkeypatch):
    from starlette.requests import Request

    import api.routers.messages as router

    pushed = []
    changed = {"value": True}

    async def fake_remove(message_id, user_id):
        return {
            "success": True,
            "changed": changed["value"],
            "conversation_id": 9,
            "participants": ["a", "b"],
            "reactions": [],
        }

    async def fake_send(user_id, payload):
        pushed.append((user_id, payload))

    monkeypatch.setattr(router.messages_repo, "remove_reaction", fake_remove)
    monkeypatch.setattr(router.message_manager, "send_to_user", fake_send)
    req = Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )
    endpoint = router.remove_reaction_endpoint.__wrapped__

    assert await endpoint(req, 5, {"user_id": "a"}) == {
        "success": True,
        "reactions": [],
    }
    assert {uid for uid, _ in pushed} == {"a", "b"}
    pushed.clear()
    changed["value"] = False
    await endpoint(req, 5, {"user_id": "a"})
    assert pushed == [], "沒有變化就不推"


async def test_remove_reaction_reports_whether_anything_changed(pg_session):
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    msg = await _msg(s, u["a"], u["b"])
    assert (await messages_repo.remove_reaction(msg["id"], u["b"], session=s))[
        "changed"
    ] is False
    await messages_repo.set_reaction(msg["id"], u["b"], "ok", session=s)
    assert (await messages_repo.remove_reaction(msg["id"], u["b"], session=s))[
        "changed"
    ] is True
