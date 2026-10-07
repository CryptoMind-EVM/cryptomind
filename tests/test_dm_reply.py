"""私訊回覆引用（2026-09-29）。

送出時可帶 reply_to_message_id（同一對話、未收回）；讀取時每則帶 reply_to 預覽
（id、發送者、暱稱、前 100 字、是否已收回）——預覽由後端截，不把原訊息全文送出去。
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
    users = {k: f"t-rp-{k}-{suffix}" for k in ("a", "b", "x")}
    for uid in users.values():
        await session.execute(
            text("INSERT INTO users (user_id, username) VALUES (:u, :u)"), {"u": uid}
        )
    await session.execute(
        text("UPDATE users SET display_name = :n WHERE user_id = :u"),
        {"n": f"Alice-{suffix}", "u": users["a"]},
    )
    try:
        yield session, users
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_reply_carries_preview_on_send_and_read(pg_session):
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    parent = (
        await messages_repo.send_message(u["a"], u["b"], "原訊息" * 60, session=s)
    )["message"]

    sent = await messages_repo.send_message(
        u["b"], u["a"], "收到", reply_to_message_id=parent["id"], session=s
    )

    assert sent["success"] is True
    preview = sent["message"]["reply_to"]
    assert preview["id"] == parent["id"]
    assert preview["from_user_id"] == u["a"]
    assert preview["from_display_name"].startswith("Alice-")
    assert preview["recalled"] is False
    assert len(preview["snippet"]) == 100, "預覽由後端截到 100 字，不送全文"
    page = await messages_repo.get_messages(
        parent["conversation_id"], u["a"], session=s
    )
    by_id = {m["id"]: m for m in page["messages"]}
    assert by_id[sent["message"]["id"]]["reply_to"] == preview
    assert by_id[parent["id"]]["reply_to"] is None


async def test_reply_target_must_be_same_conversation_and_not_recalled(pg_session):
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    other_conv = (
        await messages_repo.send_message(u["a"], u["x"], "別的對話", session=s)
    )["message"]
    mine = (await messages_repo.send_message(u["a"], u["b"], "hi", session=s))[
        "message"
    ]
    await messages_repo.recall_message(mine["id"], u["a"], session=s)

    async def reply(target):
        return await messages_repo.send_message(
            u["b"], u["a"], "re", reply_to_message_id=target, session=s
        )

    assert (await reply(other_conv["id"]))["error"] == "reply_target_not_found"
    assert (await reply(10**9))["error"] == "reply_target_not_found"
    assert (await reply(mine["id"]))["error"] == "reply_target_recalled"


async def test_quote_shows_recalled_after_parent_is_recalled(pg_session):
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    parent = (await messages_repo.send_message(u["a"], u["b"], "等等收回", session=s))[
        "message"
    ]
    child = (
        await messages_repo.send_message(
            u["b"], u["a"], "我回你", reply_to_message_id=parent["id"], session=s
        )
    )["message"]

    await messages_repo.recall_message(parent["id"], u["a"], session=s)

    page = await messages_repo.get_messages(
        parent["conversation_id"], u["b"], session=s
    )
    quote = next(m for m in page["messages"] if m["id"] == child["id"])["reply_to"]
    assert quote["recalled"] is True
    assert quote["snippet"] == ""


def test_send_request_accepts_optional_reply_target():
    from pydantic import ValidationError

    from api.routers.messages import SendMessageRequest

    assert SendMessageRequest(to_user_id="u", content="hi").reply_to_message_id is None
    assert (
        SendMessageRequest(
            to_user_id="u", content="hi", reply_to_message_id=5
        ).reply_to_message_id
        == 5
    )
    with pytest.raises(ValidationError):
        SendMessageRequest(to_user_id="u", content="hi", reply_to_message_id=0)


# /with/（第一頁）走舊 psycopg2 路徑，要 commit 才讀得到，結束時清掉
async def test_first_page_raw_path_also_has_reply_preview():
    from core.database.connection import get_connection
    from core.database.messages.helpers import get_conversation_with_messages
    from core.orm.messages_repo import messages_repo

    try:
        conn = get_connection()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 連不到：{e}")
    suffix = uuid.uuid4().hex[:8]
    a, b = f"t-rpw-a-{suffix}", f"t-rpw-b-{suffix}"
    try:
        c = conn.cursor()
        for uid in (a, b):
            c.execute(
                "INSERT INTO users (user_id, username) VALUES (%s, %s)", (uid, uid)
            )
        conn.commit()
        parent = (await messages_repo.send_message(a, b, "第一頁的原訊息"))["message"]
        child = (
            await messages_repo.send_message(
                b, a, "回", reply_to_message_id=parent["id"]
            )
        )["message"]

        page = get_conversation_with_messages(a, b)

        quote = next(m for m in page["messages"] if m["id"] == child["id"])["reply_to"]
        assert quote == child["reply_to"]
        assert quote["snippet"] == "第一頁的原訊息"
    finally:
        c = conn.cursor()
        c.execute("DELETE FROM dm_messages WHERE from_user_id IN (%s, %s)", (a, b))
        c.execute("DELETE FROM dm_conversations WHERE user1_id IN (%s, %s)", (a, b))
        c.execute("DELETE FROM users WHERE user_id IN (%s, %s)", (a, b))
        conn.commit()
        conn.close()


# review 2026-09-29：回覆目標不合法要在扣今日額度之前擋下，不然每次失敗都白扣一則
async def test_invalid_reply_target_does_not_burn_daily_quota(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    import api.routers.messages as router

    burned = []

    async def valid(user_id, to_user_id):
        return {"valid": True}

    async def bad_target(from_user_id, to_user_id, reply_to_message_id):
        return "reply_target_recalled"

    async def run_sync(fn, *args):
        return fn(*args)

    monkeypatch.setattr(router.messages_repo, "validate_message_send", valid)
    monkeypatch.setattr(router.messages_repo, "check_reply_target", bad_target)
    monkeypatch.setattr(router, "run_sync", run_sync)
    monkeypatch.setattr(
        router, "get_user_membership", lambda uid: {"is_premium": False}
    )
    monkeypatch.setattr(
        router, "check_and_increment_message", lambda uid, p: burned.append(uid)
    )
    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )
    body = router.SendMessageRequest(
        to_user_id="b", content="hi", reply_to_message_id=7
    )

    with pytest.raises(HTTPException) as exc:
        await router.send_message_endpoint.__wrapped__(req, body, {"user_id": "a"})

    assert exc.value.status_code == 400
    assert exc.value.detail == "reply_target_recalled"
    assert burned == [], "額度不能在回覆目標被擋下之前就扣掉"


async def test_check_reply_target(pg_session):
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    other = (await messages_repo.send_message(u["a"], u["x"], "x", session=s))[
        "message"
    ]
    mine = (await messages_repo.send_message(u["a"], u["b"], "hi", session=s))[
        "message"
    ]

    async def check(target):
        return await messages_repo.check_reply_target(u["b"], u["a"], target, session=s)

    assert await check(mine["id"]) is None
    assert await check(other["id"]) == "reply_target_not_found"
    await messages_repo.recall_message(mine["id"], u["a"], session=s)
    assert await check(mine["id"]) == "reply_target_recalled"
