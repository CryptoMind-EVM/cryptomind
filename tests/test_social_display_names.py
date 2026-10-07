"""社群功能顯示暱稱（2026-09-29）。

好友列表、搜尋、私訊、通知以前只回帳號名（EVM_xxxx）：使用者改了暱稱，朋友在聊天室
看到的還是 EVM_xxxx。現在一律多帶 display_name（前端顯示暱稱優先、附 @帳號名），
搜尋也比對暱稱。個人頁的好友欄位用欄位名取值（前面加欄位不再錯位）。真 PostgreSQL，
交易內跑完 rollback。
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
async def pg():
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
    except Exception as e:  # noqa: BLE001 — 連不到就 skip
        await engine.dispose()
        pytest.skip(f"PostgreSQL 連不到：{e}")
    trans = await conn.begin()
    s = AsyncSession(bind=conn, expire_on_commit=False)
    sfx = uuid.uuid4().hex[:8]
    me, friend = f"t-dn-me-{sfx}", f"t-dn-fr-{sfx}"
    nick = f"小明{sfx}"
    await s.execute(
        text("INSERT INTO users (user_id, username) VALUES (:u, :n)"),
        {"u": me, "n": f"EVM_me{sfx}"},
    )
    await s.execute(
        text("INSERT INTO users (user_id, username, display_name) VALUES (:u, :n, :d)"),
        {"u": friend, "n": f"EVM_fr{sfx}", "d": nick},
    )
    await s.execute(
        text(
            "INSERT INTO friendships (user_id, friend_id, status, created_at, updated_at) "
            "VALUES (:a, :b, 'accepted', NOW(), NOW())"
        ),
        {"a": me, "b": friend},
    )
    try:
        yield s, me, friend, nick, sfx
    finally:
        await s.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_friends_list_carries_display_name(pg):
    from core.orm.friends_repo import friends_repo

    s, me, friend, nick, _ = pg
    rows = await friends_repo.get_friends_list(me, session=s)
    row = next(r for r in rows if r["user_id"] == friend)
    assert row["display_name"] == nick
    assert row["username"].startswith("EVM_fr"), "帳號名照樣帶著（前端附 @帳號名）"


async def test_search_matches_nickname(pg):
    from core.orm.friends_repo import friends_repo

    s, me, friend, nick, sfx = pg
    found = await friends_repo.search_users(nick[:6], exclude_user_id=me, session=s)
    assert any(u["user_id"] == friend and u["display_name"] == nick for u in found), (
        "朋友只記得暱稱：用暱稱要搜得到"
    )


async def test_profile_has_display_name_and_friend_fields_line_up(pg):
    from core.orm.friends_repo import friends_repo

    s, me, friend, nick, _ = pg
    as_friend = await friends_repo.get_public_user_profile(
        friend, viewer_user_id=me, session=s
    )
    assert as_friend["display_name"] == nick
    assert as_friend["friend_status"] == "accepted", "好友欄位沒有因為多一欄而錯位"
    assert as_friend["is_friend"] is True

    as_guest = await friends_repo.get_public_user_profile(
        friend, viewer_user_id=None, session=s
    )
    assert as_guest["display_name"] == nick
