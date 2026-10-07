"""封鎖／解除封鎖（2026-09-29，c062）。

以前封鎖會刪掉好友關係、解除封鎖不會補回：兩個原本的好友解除封鎖後就不能再私訊。
現在照 LINE：封鎖前是好友，解除封鎖就恢復好友（封鎖列記 restore_on_unblock）。
另外修掉：A 先封鎖 B、B 再封鎖 A 時，A 的封鎖被 B 的封鎖一起刪掉（A 就不再封鎖 B 了）。
一對人只能有一列（idx_friendships_ordered_pair），互相封鎖記在 mutual_block。
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
    users = {k: f"t-blk-{k}-{suffix}" for k in ("a", "b")}
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


async def _befriend(s, a, b):
    from sqlalchemy import text

    await s.execute(
        text(
            "INSERT INTO friendships (user_id, friend_id, status) VALUES (:a, :b, 'accepted')"
        ),
        {"a": a, "b": b},
    )


async def _rows(s, u):
    from sqlalchemy import text

    rows = await s.execute(
        text(
            "SELECT user_id, friend_id, status, restore_on_unblock, mutual_block FROM friendships "
            "WHERE user_id IN (:a, :b) AND friend_id IN (:a, :b) ORDER BY user_id"
        ),
        {"a": u["a"], "b": u["b"]},
    )
    who = {u["a"]: "a", u["b"]: "b"}
    return sorted((who[r[0]], who[r[1]], r[2], r[3], r[4]) for r in rows.all())


async def test_unblock_restores_former_friend(pg_session):
    from core.orm.friends_repo import friends_repo

    s, u = pg_session
    await _befriend(s, u["b"], u["a"])  # 好友列方向跟封鎖方向相反也要認得

    await friends_repo.block_user(u["a"], u["b"], session=s)
    assert await _rows(s, u) == [("a", "b", "blocked", True, False)]
    assert await friends_repo.is_friend(u["a"], u["b"], session=s) is False

    result = await friends_repo.unblock_user(u["a"], u["b"], session=s)

    assert result["success"] is True
    assert result["friendship_restored"] is True
    assert await friends_repo.is_friend(u["a"], u["b"], session=s) is True
    assert await _rows(s, u) == [("a", "b", "accepted", False, False)]


async def test_unblock_does_not_befriend_strangers(pg_session):
    from core.orm.friends_repo import friends_repo

    s, u = pg_session
    await friends_repo.block_user(u["a"], u["b"], session=s)
    assert await _rows(s, u) == [("a", "b", "blocked", False, False)]

    result = await friends_repo.unblock_user(u["a"], u["b"], session=s)

    assert result == {
        "success": True,
        "message": "user_unblocked",
        "friendship_restored": False,
    }
    assert await _rows(s, u) == []


async def test_mutual_block_keeps_both_and_restores_after_both_unblock(pg_session):
    from core.orm.friends_repo import friends_repo

    s, u = pg_session
    await _befriend(s, u["a"], u["b"])
    await friends_repo.block_user(u["a"], u["b"], session=s)
    await friends_repo.block_user(u["b"], u["a"], session=s)

    assert await _rows(s, u) == [("a", "b", "blocked", True, True)], (
        "B 封鎖 A 不能把 A 對 B 的封鎖抹掉"
    )
    assert [
        x["user_id"] for x in await friends_repo.get_blocked_users(u["a"], session=s)
    ] == [u["b"]]
    assert [
        x["user_id"] for x in await friends_repo.get_blocked_users(u["b"], session=s)
    ] == [u["a"]]

    # 先封鎖的 A 解除：B 還在封鎖 → 這列翻成 B 封鎖 A，旗標帶著走，不恢復
    first = await friends_repo.unblock_user(u["a"], u["b"], session=s)
    assert first["friendship_restored"] is False
    assert await _rows(s, u) == [("b", "a", "blocked", True, False)]
    assert await friends_repo.get_blocked_users(u["a"], session=s) == []
    assert await friends_repo.is_blocked(u["a"], u["b"], session=s) is True

    second = await friends_repo.unblock_user(u["b"], u["a"], session=s)
    assert second["friendship_restored"] is True
    assert await friends_repo.is_friend(u["a"], u["b"], session=s) is True


async def test_mutual_block_later_blocker_unblocks_first(pg_session):
    from core.orm.friends_repo import friends_repo

    s, u = pg_session
    await _befriend(s, u["a"], u["b"])
    await friends_repo.block_user(u["a"], u["b"], session=s)
    await friends_repo.block_user(u["b"], u["a"], session=s)

    first = await friends_repo.unblock_user(u["b"], u["a"], session=s)  # 後封鎖的先解除
    assert first["friendship_restored"] is False
    assert await _rows(s, u) == [("a", "b", "blocked", True, False)], "A 的封鎖還在"
    assert (await friends_repo.unblock_user(u["a"], u["b"], session=s))[
        "friendship_restored"
    ]


async def test_blocked_party_cannot_unblock(pg_session):
    from core.orm.friends_repo import friends_repo

    s, u = pg_session
    await friends_repo.block_user(u["a"], u["b"], session=s)
    assert (await friends_repo.unblock_user(u["b"], u["a"], session=s))[
        "error"
    ] == "user_not_blocked"
    assert await _rows(s, u) == [("a", "b", "blocked", False, False)]


async def test_friend_request_errors_know_who_blocked(pg_session):
    from core.orm.friends_repo import friends_repo

    s, u = pg_session
    await friends_repo.block_user(u["a"], u["b"], session=s)
    assert (await friends_repo.send_friend_request(u["b"], u["a"], session=s))[
        "error"
    ] == "user_blocked_you"
    assert (await friends_repo.send_friend_request(u["a"], u["b"], session=s))[
        "error"
    ] == "you_blocked_user"
    await friends_repo.block_user(u["b"], u["a"], session=s)
    assert (await friends_repo.send_friend_request(u["b"], u["a"], session=s))[
        "error"
    ] == "you_blocked_user"


async def test_status_says_whether_i_blocked(pg_session):
    """前端只對「我封鎖的人」給解除按鈕：被對方封鎖時按了只會 400"""
    from core.orm.friends_repo import friends_repo

    s, u = pg_session
    await friends_repo.block_user(u["a"], u["b"], session=s)
    mine = await friends_repo.get_friendship_status(u["a"], u["b"], session=s)
    theirs = await friends_repo.get_friendship_status(u["b"], u["a"], session=s)
    assert (mine["status"], mine["blocked_by_me"]) == ("blocked", True)
    assert (theirs["status"], theirs["blocked_by_me"]) == ("blocked", False)
    bulk = await friends_repo.get_bulk_friendship_status(u["b"], [u["a"]], session=s)
    assert bulk[u["a"]]["blocked_by_me"] is False
    await friends_repo.block_user(u["b"], u["a"], session=s)
    theirs = await friends_repo.get_friendship_status(u["b"], u["a"], session=s)
    assert theirs["blocked_by_me"] is True


async def test_mutual_blocker_does_not_see_the_conversation(pg_session):
    """LINE 模式：封鎖的人對話從列表隱藏；互相封鎖時後封鎖的人也要隱藏"""
    from core.orm.friends_repo import friends_repo
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    await messages_repo.send_message(u["a"], u["b"], "hi", session=s)
    await friends_repo.block_user(u["a"], u["b"], session=s)
    assert await messages_repo.get_conversations(u["b"], session=s), (
        "被封鎖的一方照常看得到"
    )
    await friends_repo.block_user(u["b"], u["a"], session=s)
    assert await messages_repo.get_conversations(u["b"], session=s) == []
    assert await messages_repo.get_conversations(u["a"], session=s) == []


async def test_blocking_twice_keeps_the_restore_flag(pg_session):
    from core.orm.friends_repo import friends_repo

    s, u = pg_session
    await _befriend(s, u["a"], u["b"])
    await friends_repo.block_user(u["a"], u["b"], session=s)
    await friends_repo.block_user(u["a"], u["b"], session=s)

    assert await _rows(s, u) == [("a", "b", "blocked", True, False)]
    assert (await friends_repo.unblock_user(u["a"], u["b"], session=s))[
        "friendship_restored"
    ]


async def test_pending_request_is_not_a_friendship(pg_session):
    from sqlalchemy import text

    from core.orm.friends_repo import friends_repo

    s, u = pg_session
    await s.execute(
        text(
            "INSERT INTO friendships (user_id, friend_id, status) VALUES (:a, :b, 'pending')"
        ),
        {"a": u["b"], "b": u["a"]},
    )
    await friends_repo.block_user(u["a"], u["b"], session=s)
    result = await friends_repo.unblock_user(u["a"], u["b"], session=s)
    assert result["friendship_restored"] is False
    assert await _rows(s, u) == []


async def test_unblock_when_not_blocked(pg_session):
    from core.orm.friends_repo import friends_repo

    s, u = pg_session
    result = await friends_repo.unblock_user(u["a"], u["b"], session=s)
    assert result == {"success": False, "error": "user_not_blocked"}


async def test_report_and_block_then_unblock_restores(pg_session):
    """私訊檢舉時勾「同時封鎖」走同一個 block_user：解除後一樣恢復好友"""
    from core.orm.dm_reports_repo import dm_reports_repo
    from core.orm.friends_repo import friends_repo
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    await _befriend(s, u["a"], u["b"])
    msg = (await messages_repo.send_message(u["b"], u["a"], "騷擾", session=s))[
        "message"
    ]
    await dm_reports_repo.report_message(
        msg["id"], u["a"], "harassment", None, block=True, session=s
    )
    assert await _rows(s, u) == [("a", "b", "blocked", True, False)]

    assert (await friends_repo.unblock_user(u["a"], u["b"], session=s))[
        "friendship_restored"
    ]


# review 2026-09-29：兩人之間還沒有任何列時同時互相封鎖——SELECT FOR UPDATE 鎖不到不存在
# 的列，兩邊都 INSERT，後到的撞 idx_friendships_ordered_pair → 500。要真的交錯才驗得到：
# A 的交易還沒 commit 時 B 進來（gather 不會真的交錯）
async def test_concurrent_mutual_block_queues_instead_of_500():
    import asyncio

    from dotenv import load_dotenv
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from core.orm.friends_repo import friends_repo
    from core.orm.session import _normalize_pg_url

    load_dotenv()
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        pytest.skip("沒有 PostgreSQL DATABASE_URL")
    engine = create_async_engine(_normalize_pg_url(url))
    suffix = uuid.uuid4().hex[:8]
    a, b = f"t-blkc-a-{suffix}", f"t-blkc-b-{suffix}"
    try:
        try:
            async with engine.begin() as conn:
                for uid in (a, b):
                    await conn.execute(
                        text("INSERT INTO users (user_id, username) VALUES (:u, :u)"),
                        {"u": uid},
                    )
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"PostgreSQL 連不到：{e}")

        async with AsyncSession(engine) as s1, AsyncSession(engine) as s2:
            await friends_repo.block_user(a, b, session=s1)  # 還沒 commit
            second = asyncio.create_task(friends_repo.block_user(b, a, session=s2))
            await asyncio.sleep(0.5)
            assert not second.done(), "B 要排隊等 A 的交易，不是自己先插一列"
            await s1.commit()
            assert (await second)["success"] is True
            await s2.commit()

        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT user_id, status, mutual_block FROM friendships "
                        "WHERE user_id IN (:a, :b) AND friend_id IN (:a, :b)"
                    ),
                    {"a": a, "b": b},
                )
            ).all()
        assert [tuple(r) for r in rows] == [(a, "blocked", True)]
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM friendships WHERE user_id IN (:a, :b) OR friend_id IN (:a, :b)"
                ),
                {"a": a, "b": b},
            )
            await conn.execute(
                text("DELETE FROM users WHERE user_id IN (:a, :b)"), {"a": a, "b": b}
            )
        await engine.dispose()


def test_legacy_raw_conversation_query_still_runs():
    """舊的 psycopg2 版對話列表也改成認得 mutual_block（多了兩個參數），佔位符數量要對得上"""
    from core.database.messages.conversations import get_conversations

    try:
        assert isinstance(get_conversations(f"nobody-{uuid.uuid4().hex[:6]}"), list)
    except Exception as e:  # noqa: BLE001
        if "connect" in str(e).lower():
            pytest.skip(f"PostgreSQL 連不到：{e}")
        raise
