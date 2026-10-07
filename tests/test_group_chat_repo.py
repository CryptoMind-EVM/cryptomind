"""群組聊天：群組、成員、邀請（core/orm/group_chat_repo.py，設計 docs/plans/2026-10-01-group-chat-design.md）。

真 PostgreSQL、交易最後 rollback；併發那題自己開兩條連線、跑完清掉。
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）


async def _group(s, owner, name="投資閒聊"):
    from core.orm.group_chat_repo import group_chat_repo

    result = await group_chat_repo.create_group(owner, name, session=s)
    assert result["success"] is True, result
    return result["group"]["id"]


async def _join(s, make, gid, inviter, invitee):
    """邀請＋接受（雙方先當好友）"""
    from core.orm.group_chat_repo import group_chat_repo

    await make.friends(inviter, invitee)
    inv = await group_chat_repo.create_invites(gid, inviter, [invitee], session=s)
    assert inv["invited"], inv
    acc = await group_chat_repo.accept_invite(
        inv["invited"][0]["invite_id"], invitee, session=s
    )
    assert acc["success"] is True, acc
    return acc


async def _messages(s, gid):
    from sqlalchemy import text

    rows = await s.execute(
        text(
            "SELECT content, message_type FROM group_messages WHERE group_id=:g ORDER BY id"
        ),
        {"g": gid},
    )
    return [tuple(r) for r in rows]


# ── 開群 ──────────────────────────────────────────────


async def test_create_group_makes_owner_member_and_system_message(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, _ = gc_pg
    result = await group_chat_repo.create_group(u["a"], "  投資閒聊  ", session=s)
    assert result["success"] is True
    group = result["group"]
    assert (
        group["name"] == "投資閒聊"
        and group["owner_id"] == u["a"]
        and group["member_count"] == 1
    )
    assert await group_chat_repo.member_ids(group["id"], session=s) == [u["a"]]
    assert await _messages(s, group["id"]) == [(f"created:{u['a']}", "system")]


@pytest.mark.parametrize("name", ["", "   ", "x" * 31])
async def test_create_group_rejects_bad_names(gc_pg, name):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, _ = gc_pg
    result = await group_chat_repo.create_group(u["a"], name, session=s)
    assert result == {"success": False, "error": "invalid_name"}


async def test_create_and_join_limits(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    await make.config("limit_group_create", "2")
    await make.config("limit_group_join", "3")
    await _group(s, u["a"], "一")
    await _group(s, u["a"], "二")
    result = await group_chat_repo.create_group(u["a"], "三", session=s)
    assert result == {"success": False, "error": "create_limit_reached"}

    # 建群上限放寬後：b 在 c 的群裡＋自己開兩個＝加入 3 個，再開會超過加入上限
    await make.config("limit_group_create", "10")
    gid = await _group(s, u["c"], "c 的群")
    await _join(s, make, gid, u["c"], u["b"])
    await _group(s, u["b"], "b1")
    await _group(s, u["b"], "b2")
    result = await group_chat_repo.create_group(u["b"], "b3", session=s)
    assert result == {"success": False, "error": "join_limit_reached"}


# ── 讀取 ──────────────────────────────────────────────


async def test_get_group_hides_from_non_members(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    await _join(s, make, gid, u["a"], u["b"])
    assert await group_chat_repo.get_group(gid, u["c"], session=s) is None
    info = await group_chat_repo.get_group(gid, u["b"], session=s)
    assert info["name"] == "投資閒聊" and info["history_visible"] is False
    members = {m["user_id"]: m for m in info["members"]}
    assert set(members) == {u["a"], u["b"]}
    assert members[u["a"]]["is_owner"] is True and members[u["b"]]["is_owner"] is False
    assert info["me"]["muted"] is False


async def test_list_groups_unread_and_preview(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    await _join(s, make, gid, u["a"], u["b"])
    # b 自己送的那則同時把讀取位置推到那裡（人在聊天室裡才送得出來），之後 a 的兩則才是未讀
    await group_messages_repo.send_message(gid, u["b"], "我自己的", session=s)
    await group_messages_repo.send_message(gid, u["a"], "第一則", session=s)
    await group_messages_repo.send_message(gid, u["a"], "第二則", session=s)

    (row,) = await group_chat_repo.list_groups(u["b"], session=s)
    assert row["id"] == gid and row["member_count"] == 2
    assert row["unread_count"] == 2, "自己的訊息與系統訊息不算未讀"
    assert row["last_message"]["content"] == "第二則"
    assert await group_chat_repo.list_groups(u["c"], session=s) == []


# ── 群主操作 ──────────────────────────────────────────


async def test_update_group_owner_only_with_system_messages(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    await _join(s, make, gid, u["a"], u["b"])

    assert (await group_chat_repo.update_group(gid, u["b"], name="新名", session=s))[
        "error"
    ] == "not_owner"
    assert (await group_chat_repo.update_group(gid, u["c"], name="新名", session=s))[
        "error"
    ] == "not_found"
    assert (await group_chat_repo.update_group(gid, u["a"], name="", session=s))[
        "error"
    ] == "invalid_name"

    result = await group_chat_repo.update_group(
        gid, u["a"], name="新名", history_visible=True, session=s
    )
    assert result["success"] is True and result["group"]["name"] == "新名"
    assert [m["content"] for m in result["system_messages"]] == [
        "renamed:新名",
        "history_visible:on",
    ]

    same = await group_chat_repo.update_group(
        gid, u["a"], name="新名", history_visible=True, session=s
    )
    assert same["system_messages"] == [], "沒變就不發系統訊息"


async def test_remove_member_rules(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    await _join(s, make, gid, u["a"], u["b"])
    await _join(s, make, gid, u["a"], u["c"])

    assert (await group_chat_repo.remove_member(gid, u["b"], u["c"], session=s))[
        "error"
    ] == "not_owner"
    assert (await group_chat_repo.remove_member(gid, u["a"], u["a"], session=s))[
        "error"
    ] == "cannot_remove_self"
    assert (await group_chat_repo.remove_member(gid, u["a"], u["d"], session=s))[
        "error"
    ] == "target_not_member"

    result = await group_chat_repo.remove_member(gid, u["a"], u["c"], session=s)
    assert result["success"] is True
    assert result["system_message"]["content"] == f"member_removed:{u['c']}"
    assert sorted(result["member_ids"]) == sorted([u["a"], u["b"]])
    assert await group_chat_repo.get_group(gid, u["c"], session=s) is None


async def test_owner_leaving_transfers_to_earliest_member_and_last_leaver_deletes(
    gc_pg,
):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    await _join(s, make, gid, u["a"], u["b"])
    await _join(s, make, gid, u["a"], u["c"])

    result = await group_chat_repo.leave_group(gid, u["a"], session=s)
    assert result["success"] is True and result["deleted"] is False
    assert result["new_owner_id"] == u["b"], "最早入群的接手"
    assert [m["content"] for m in result["system_messages"]] == [
        f"member_left:{u['a']}",
        f"owner_changed:{u['b']}",
    ]
    assert (await group_chat_repo.get_group(gid, u["b"], session=s))["owner_id"] == u[
        "b"
    ]

    await group_chat_repo.leave_group(gid, u["c"], session=s)
    last = await group_chat_repo.leave_group(gid, u["b"], session=s)
    assert last["deleted"] is True
    assert (await group_chat_repo.leave_group(gid, u["b"], session=s))[
        "error"
    ] == "not_found"


async def test_transfer_owner_rules(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    await _join(s, make, gid, u["a"], u["b"])
    await _join(s, make, gid, u["a"], u["c"])

    def err(result):
        return result.get("error")

    assert (
        err(await group_chat_repo.transfer_owner(gid, u["b"], u["c"], session=s))
        == "not_owner"
    )
    assert (
        err(await group_chat_repo.transfer_owner(gid, u["a"], u["a"], session=s))
        == "cannot_transfer_to_self"
    )
    assert (
        err(await group_chat_repo.transfer_owner(gid, u["a"], u["d"], session=s))
        == "target_not_member"
    )
    assert (
        err(await group_chat_repo.transfer_owner(gid, u["d"], u["b"], session=s))
        == "not_found"
    ), "非成員不透露群組存在"
    await make.set_premium(u["c"], False)
    assert (
        err(await group_chat_repo.transfer_owner(gid, u["a"], u["c"], session=s))
        == "target_not_premium"
    ), "Pro 到期的人唯讀，接了群主也什麼都不能管"

    result = await group_chat_repo.transfer_owner(gid, u["a"], u["b"], session=s)
    assert result["success"] is True, result
    assert result["system_message"]["content"] == f"owner_changed:{u['b']}"
    assert sorted(result["member_ids"]) == sorted([u["a"], u["b"], u["c"]])
    group = await group_chat_repo.get_group(gid, u["a"], session=s)
    assert group["owner_id"] == u["b"]
    assert [m["user_id"] for m in group["members"] if m["is_owner"]] == [u["b"]]
    assert (
        err(await group_chat_repo.transfer_owner(gid, u["a"], u["c"], session=s))
        == "not_owner"
    ), "交出去就不是群主了"


async def test_set_muted(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    await _join(s, make, gid, u["a"], u["b"])
    assert sorted(await group_chat_repo.unmuted_member_ids(gid, session=s)) == sorted(
        [u["a"], u["b"]]
    )
    assert (await group_chat_repo.set_muted(gid, u["a"], True, session=s))[
        "success"
    ] is True
    assert await group_chat_repo.unmuted_member_ids(gid, session=s) == [u["b"]], (
        "關了通知的人不發"
    )
    assert (await group_chat_repo.get_group(gid, u["a"], session=s))["me"][
        "muted"
    ] is True
    assert (await group_chat_repo.set_muted(gid, u["c"], True, session=s))[
        "error"
    ] == "not_found"


# ── 邀請 ──────────────────────────────────────────────


async def test_invite_skip_reasons(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    free = await make.user("free", premium=False)
    await make.friends(u["a"], u["b"], u["c"], free)
    await make.block(u["a"], u["d"])
    await _join(s, make, gid, u["a"], u["e"])  # 已是成員
    await group_chat_repo.create_invites(
        gid, u["a"], [u["c"]], session=s
    )  # 已有待處理邀請

    result = await group_chat_repo.create_invites(
        gid, u["a"], [u["a"], u["b"], u["c"], u["d"], u["e"], u["f"], free], session=s
    )
    assert [i["invitee_id"] for i in result["invited"]] == [u["b"]]
    reasons = {x["user_id"]: x["reason"] for x in result["skipped"]}
    assert reasons == {
        u["a"]: "self",
        u["c"]: "already_invited",
        u["d"]: "blocked",
        u["e"]: "already_member",
        u["f"]: "not_friend",
        free: "not_premium",
    }

    assert (await group_chat_repo.create_invites(gid, u["f"], [u["b"]], session=s))[
        "error"
    ] == "not_found"


async def test_invitee_at_join_limit_is_skipped(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    await make.config("limit_group_join", "1")
    gid = await _group(s, u["a"])
    await _group(s, u["b"], "b 自己的")  # b 已加入 1 個＝上限
    await make.friends(u["a"], u["b"])
    result = await group_chat_repo.create_invites(gid, u["a"], [u["b"]], session=s)
    assert result["skipped"] == [{"user_id": u["b"], "reason": "join_limit_reached"}]


async def test_daily_invite_limit(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    await make.config("limit_daily_group_invites", "2")
    gid = await _group(s, u["a"])
    await make.friends(u["a"], u["b"], u["c"], u["d"])
    result = await group_chat_repo.create_invites(
        gid, u["a"], [u["b"], u["c"], u["d"]], session=s
    )
    assert len(result["invited"]) == 2
    assert result["skipped"] == [
        {"user_id": u["d"], "reason": "daily_invite_limit_reached"}
    ]
    again = await group_chat_repo.create_invites(gid, u["a"], [u["d"]], session=s)
    assert again == {"success": False, "error": "daily_invite_limit_reached"}


async def test_list_accept_decline_invites(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    await make.friends(u["a"], u["b"], u["c"])
    inv = await group_chat_repo.create_invites(gid, u["a"], [u["b"], u["c"]], session=s)
    ids = {i["invitee_id"]: i["invite_id"] for i in inv["invited"]}

    (pending,) = await group_chat_repo.list_invites(u["b"], session=s)
    assert pending["group_id"] == gid and pending["group_name"] == "投資閒聊"
    assert pending["inviter_id"] == u["a"] and pending["member_count"] == 1

    assert (await group_chat_repo.accept_invite(ids[u["b"]], u["c"], session=s))[
        "error"
    ] == "invite_not_found"
    acc = await group_chat_repo.accept_invite(ids[u["b"]], u["b"], session=s)
    assert (
        acc["success"] is True
        and acc["system_message"]["content"] == f"member_joined:{u['b']}"
    )
    assert sorted(acc["member_ids"]) == sorted([u["a"], u["b"]])
    assert (await group_chat_repo.accept_invite(ids[u["b"]], u["b"], session=s))[
        "error"
    ] == "invite_not_found"

    dec = await group_chat_repo.decline_invite(ids[u["c"]], u["c"], session=s)
    assert dec == {"success": True, "group_id": gid, "inviter_id": u["a"]}
    assert await group_chat_repo.list_invites(u["c"], session=s) == []


async def test_accept_requires_premium_and_space(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    await make.config("limit_group_members", "2")
    gid = await _group(s, u["a"])
    await make.friends(u["a"], u["b"], u["c"])
    inv = await group_chat_repo.create_invites(gid, u["a"], [u["b"], u["c"]], session=s)
    ids = {i["invitee_id"]: i["invite_id"] for i in inv["invited"]}

    await make.set_premium(u["b"], False)
    assert (await group_chat_repo.accept_invite(ids[u["b"]], u["b"], session=s))[
        "error"
    ] == "not_premium"
    await make.set_premium(u["b"], True)
    assert (await group_chat_repo.accept_invite(ids[u["b"]], u["b"], session=s))[
        "success"
    ] is True
    assert (await group_chat_repo.accept_invite(ids[u["c"]], u["c"], session=s))[
        "error"
    ] == "group_full"
    full = await group_chat_repo.create_invites(gid, u["a"], [u["d"]], session=s)
    assert full == {"success": False, "error": "group_full"}


async def test_history_visibility_on_join(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    before = (await group_messages_repo.send_message(gid, u["a"], "入群前", session=s))[
        "message"
    ]["id"]
    await _join(s, make, gid, u["a"], u["b"])
    me_b = (await group_chat_repo.get_group(gid, u["b"], session=s))["me"]
    assert me_b["first_visible_message_id"] >= before, "預設看不到入群前的訊息"

    await group_chat_repo.update_group(gid, u["a"], history_visible=True, session=s)
    await _join(s, make, gid, u["a"], u["c"])
    me_c = (await group_chat_repo.get_group(gid, u["c"], session=s))["me"]
    assert me_c["first_visible_message_id"] == 0, "群主開了歷史紀錄，之後入群的人從頭看"
    me_b_again = (await group_chat_repo.get_group(gid, u["b"], session=s))["me"]
    assert me_b_again["first_visible_message_id"] == me_b["first_visible_message_id"], (
        "已在群裡的人不受影響"
    )


# ── 併發：剩 1 個位子，兩人同時接受只能進一個 ──────────────


@pytest.fixture
async def committed_engine():
    from dotenv import load_dotenv
    from sqlalchemy.ext.asyncio import create_async_engine

    from core.orm.session import _normalize_pg_url

    load_dotenv()
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        pytest.skip("沒有 PostgreSQL DATABASE_URL")
    engine = create_async_engine(_normalize_pg_url(url))
    try:
        async with engine.connect():
            pass
    except Exception as e:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"PostgreSQL 連不到：{e}")
    yield engine
    await engine.dispose()


async def test_concurrent_accepts_respect_member_limit(committed_engine):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.orm.group_chat_repo import group_chat_repo

    sfx = uuid.uuid4().hex[:8]
    owner, x, y = (f"t-gcc-{k}-{sfx}" for k in ("o", "x", "y"))
    async with AsyncSession(committed_engine, expire_on_commit=False) as setup:
        for uid in (owner, x, y):
            await setup.execute(
                text(
                    "INSERT INTO users (user_id, username, membership_tier) VALUES (:u, :u, 'premium')"
                ),
                {"u": uid},
            )
        for other in (x, y):
            await setup.execute(
                text(
                    "INSERT INTO friendships (user_id, friend_id, status) VALUES (:a, :b, 'accepted')"
                ),
                {"a": owner, "b": other},
            )
        await setup.commit()
        gid = (await group_chat_repo.create_group(owner, "搶位", session=setup))[
            "group"
        ]["id"]
        inv = await group_chat_repo.create_invites(gid, owner, [x, y], session=setup)
        ids = {i["invitee_id"]: i["invite_id"] for i in inv["invited"]}
        # 上限 2：群主＋一個人
        await setup.execute(
            text(
                "INSERT INTO system_config (key, value, value_type, category) VALUES ('limit_group_members', '2', 'int', 'limits') "
                "ON CONFLICT (key) DO UPDATE SET value = '2'"
            )
        )
        await setup.commit()

    try:
        async with (
            AsyncSession(committed_engine, expire_on_commit=False) as sa,
            AsyncSession(committed_engine, expire_on_commit=False) as sb,
        ):
            first = await group_chat_repo.accept_invite(
                ids[x], x, session=sa
            )  # 拿到鎖、還沒 commit
            assert first["success"] is True
            second = asyncio.create_task(
                group_chat_repo.accept_invite(ids[y], y, session=sb)
            )
            await asyncio.sleep(0.5)
            assert not second.done(), "第二個人要等第一個人的交易結束（鎖群組列）"
            await sa.commit()
            result = await asyncio.wait_for(second, timeout=10)
            assert result == {"success": False, "error": "group_full"}
            await sb.rollback()
    finally:
        async with AsyncSession(committed_engine) as cleanup:
            await cleanup.execute(
                text("DELETE FROM group_chats WHERE id = :g"), {"g": gid}
            )
            await cleanup.execute(
                text("DELETE FROM friendships WHERE user_id = :o"), {"o": owner}
            )
            await cleanup.execute(
                text("DELETE FROM users WHERE user_id IN (:a, :b, :c)"),
                {"a": owner, "b": x, "c": y},
            )
            await cleanup.execute(
                text(
                    "UPDATE system_config SET value = '50' WHERE key = 'limit_group_members'"
                )
            )
            await cleanup.commit()


async def test_last_member_leaving_cancels_pending_invites(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u["a"])
    await make.friends(u["a"], u["b"])
    inv = await group_chat_repo.create_invites(gid, u["a"], [u["b"]], session=s)
    result = await group_chat_repo.leave_group(gid, u["a"], session=s)
    assert result["deleted"] is True
    assert result["cancelled_invites"] == [
        {"invite_id": inv["invited"][0]["invite_id"], "invitee_id": u["b"]}
    ]
