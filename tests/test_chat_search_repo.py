"""聊天搜尋（core/orm/chat_search_repo.py）：只搜自己看得到的。

重點是可見範圍跟列表／聊天室一致：私訊排除自己刪的、收回的、我封鎖的對話；群組只搜目前所在的群、
first_visible_message_id 之後的 text；別人的對話與群組一律不出現（IDOR）。真 PostgreSQL、rollback。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from core.orm.chat_search_repo import like_pattern, make_snippet
from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


# ── 小工具 ──────────────────────────────────────────────


async def _search(s, uid, q, include_groups=True):
    from core.orm.chat_search_repo import chat_search_repo

    return await chat_search_repo.search(
        uid, q, include_groups=include_groups, session=s
    )


async def _dm(s, a, b, content):
    from core.orm.messages_repo import messages_repo

    result = await messages_repo.send_message(a, b, content, session=s)
    assert result["success"] is True, result
    return result["message"]


async def _hide(s, message_id, uid):
    await s.execute(
        text("INSERT INTO dm_message_deletions (message_id, user_id) VALUES (:m, :u)"),
        {"m": message_id, "u": uid},
    )


async def _nickname(s, uid, name):
    await s.execute(
        text("UPDATE users SET display_name=:n WHERE user_id=:u"), {"n": name, "u": uid}
    )


async def _group(s, u, make, name, owner="a", *members):
    from core.orm.group_chat_repo import group_chat_repo

    gid = (await group_chat_repo.create_group(u[owner], name, session=s))["group"]["id"]
    for m in members:
        await _join(s, u, make, gid, owner, m)
    return gid


async def _join(s, u, make, gid, owner, member):
    from core.orm.group_chat_repo import group_chat_repo

    # 邀請要先是好友（已經是就不重複加：一對人只能有一列）
    await s.execute(
        text(
            "INSERT INTO friendships (user_id, friend_id, status) "
            "VALUES (:a, :b, 'accepted') ON CONFLICT DO NOTHING"
        ),
        {"a": u[owner], "b": u[member]},
    )
    inv = await group_chat_repo.create_invites(gid, u[owner], [u[member]], session=s)
    result = await group_chat_repo.accept_invite(
        inv["invited"][0]["invite_id"], u[member], session=s
    )
    assert result["success"] is True, result


async def _gsend(s, gid, uid, content):
    from core.orm.group_messages_repo import group_messages_repo

    result = await group_messages_repo.send_message(gid, uid, content, session=s)
    assert result["success"] is True, result
    return result["message"]


async def _set_time(s, table, message_id, when):
    await s.execute(
        text(f"UPDATE {table} SET created_at=:t WHERE id=:i"),
        {"t": when, "i": message_id},
    )


def _ids(items, key="message_id"):
    return [i[key] for i in items]


# ── 聯絡人 ──────────────────────────────────────────────


async def test_contacts_by_username_and_nickname(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"], u["c"])
    nick = f"小明{make.suffix}"
    await _nickname(s, u["c"], nick)

    # 帳號名、不分大小寫；沒聊過＝conversation_id 是 None
    res = await _search(s, u["a"], u["b"].upper())
    assert res["contacts"] == [
        {
            "user_id": u["b"],
            "username": u["b"],
            "display_name": None,
            "conversation_id": None,
        }
    ]
    # 暱稱片段
    res = await _search(s, u["a"], f"明{make.suffix}")
    assert [c["user_id"] for c in res["contacts"]] == [u["c"]]
    assert res["contacts"][0]["display_name"] == nick
    # 自己不算聯絡人（就算有髒資料：自己跟自己的好友列）
    await make.friends(u["a"], u["a"])
    assert (await _search(s, u["a"], u["a"]))["contacts"] == []


async def test_contacts_are_friends_and_dm_partners_only(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    conv_b = (await _dm(s, u["a"], u["b"], "嗨"))["conversation_id"]
    # e 不是好友但傳過訊息（打招呼）：算私訊對象
    conv_e = (await _dm(s, u["e"], u["a"], "你好"))["conversation_id"]

    found = {
        c["user_id"]: c for c in (await _search(s, u["a"], make.suffix))["contacts"]
    }
    assert set(found) == {u["b"], u["e"]}, "陌生人（c、d、f）不該出現"
    assert found[u["b"]]["conversation_id"] == conv_b
    assert found[u["e"]]["conversation_id"] == conv_e


async def test_contacts_conversation_hidden_after_delete_for_me(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    await _hide(s, (await _dm(s, u["a"], u["b"], "嗨"))["id"], u["a"])
    await _hide(s, (await _dm(s, u["e"], u["a"], "你好"))["id"], u["a"])

    found = {
        c["user_id"]: c for c in (await _search(s, u["a"], make.suffix))["contacts"]
    }
    # 好友還在，但整段刪掉的對話不再給 id；非好友的私訊對象整個消失
    assert set(found) == {u["b"]}
    assert found[u["b"]]["conversation_id"] is None
    # 對方沒刪：b 那邊照常
    found_b = (await _search(s, u["b"], u["a"]))["contacts"]
    assert found_b[0]["conversation_id"] is not None


async def test_contacts_exclude_blocked_either_direction(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["d"])
    await _dm(s, u["a"], u["b"], "嗨")
    await _dm(s, u["c"], u["a"], "嗨")
    await make.block(u["a"], u["b"])  # 我封鎖 b
    await make.block(u["c"], u["a"])  # c 封鎖我

    found = [c["user_id"] for c in (await _search(s, u["a"], make.suffix))["contacts"]]
    assert found == [u["d"]]
    assert (await _search(s, u["b"], u["a"]))["contacts"] == []
    assert (await _search(s, u["c"], u["a"]))["contacts"] == []


async def test_contacts_limit(gc_pg):
    s, u, make = gc_pg
    extra = [await make.user(f"x{i}") for i in range(12)]
    await make.friends(u["a"], *extra)
    res = await _search(s, u["a"], f"-{make.suffix}")
    assert len(res["contacts"]) == 10


# ── 群組 ──────────────────────────────────────────────


async def test_groups_only_my_groups(gc_pg):
    s, u, make = gc_pg
    mine = await _group(s, u, make, f"投資閒聊{make.suffix}", "a", "b")
    await _group(s, u, make, f"投資閒聊{make.suffix}", "c", "d")  # a 不在裡面

    res = await _search(s, u["a"], f"閒聊{make.suffix}")
    assert res["groups"] == [
        {"id": mine, "name": f"投資閒聊{make.suffix}", "member_count": 2}
    ]


async def test_groups_flag_off_returns_dm_only(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    gid = await _group(s, u, make, f"關鍵字{make.suffix}群", "a", "b")
    await _gsend(s, gid, u["b"], f"群組 關鍵字{make.suffix}")
    dm = await _dm(s, u["b"], u["a"], f"私訊 關鍵字{make.suffix}")

    on = await _search(s, u["a"], f"關鍵字{make.suffix}")
    assert {m["kind"] for m in on["messages"]} == {"dm", "group"}
    assert len(on["groups"]) == 1

    off = await _search(s, u["a"], f"關鍵字{make.suffix}", include_groups=False)
    assert off["groups"] == []
    assert _ids(off["messages"]) == [dm["id"]]
    assert off["messages"][0]["kind"] == "dm"


# ── 私訊訊息 ──────────────────────────────────────────────


async def test_dm_messages_shape_and_names(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    await _nickname(s, u["b"], f"阿B{make.suffix}")
    msg = await _dm(s, u["b"], u["a"], "明天 BTC 會漲嗎")

    (item,) = (await _search(s, u["a"], "btc"))["messages"]
    assert item == {
        "kind": "dm",
        "message_id": msg["id"],
        "conversation_id": msg["conversation_id"],
        "group_id": None,
        "other_user_id": u["b"],
        "chat_name": f"阿B{make.suffix}",
        "from_user_id": u["b"],
        "from_name": f"阿B{make.suffix}",
        "snippet": "明天 BTC 會漲嗎",
        "created_at": item["created_at"],
    }
    assert datetime.fromisoformat(item["created_at"])
    # 自己發的：from_name 是自己（沒暱稱＝帳號名），chat_name 仍是對方
    await _dm(s, u["a"], u["b"], "我覺得 btc 不會")
    mine = (await _search(s, u["a"], "不會"))["messages"][0]
    assert mine["from_name"] == u["a"] and mine["chat_name"] == f"阿B{make.suffix}"


async def test_dm_messages_exclude_hidden_and_recalled(gc_pg):
    from core.orm.messages_repo import messages_repo

    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    keep = await _dm(s, u["a"], u["b"], "kw 留著")
    recalled = await _dm(s, u["a"], u["b"], "kw 收回的秘密")
    hidden = await _dm(s, u["b"], u["a"], "kw a 自己刪的")
    assert (await messages_repo.recall_message(recalled["id"], u["a"], session=s))[
        "success"
    ]
    await _hide(s, hidden["id"], u["a"])

    assert _ids((await _search(s, u["a"], "kw"))["messages"]) == [keep["id"]]
    # 刪除只對刪的人隱藏；收回兩邊都沒有
    assert set(_ids((await _search(s, u["b"], "kw"))["messages"])) == {
        keep["id"],
        hidden["id"],
    }
    assert (await _search(s, u["a"], "秘密"))["messages"] == []


async def test_dm_messages_never_leak_other_conversations(gc_pg):
    s, u, make = gc_pg
    await _dm(s, u["c"], u["d"], f"別人的秘密{make.suffix}")
    for uid in (u["a"], u["b"], u["e"]):
        assert (await _search(s, uid, f"秘密{make.suffix}"))["messages"] == []
    assert len((await _search(s, u["c"], f"秘密{make.suffix}"))["messages"]) == 1


async def test_dm_messages_blocked_conversation(gc_pg):
    s, u, make = gc_pg
    await _dm(s, u["a"], u["b"], "kw 跟 b")
    await _dm(s, u["a"], u["c"], "kw 跟 c")
    await make.block(u["a"], u["b"])  # 我封鎖 b：列表看不到，搜尋也不到
    await make.block(u["c"], u["a"])  # c 封鎖我、我沒封鎖 c

    assert (await _search(s, u["a"], "跟 b"))["messages"] == []
    assert (await _search(s, u["c"], "kw"))["messages"] == []
    # 被封鎖的一方：同私訊列表（LINE 式，不讓被封鎖的人察覺），對話還在
    assert len((await _search(s, u["b"], "跟 b"))["messages"]) == 1
    assert len((await _search(s, u["a"], "跟 c"))["messages"]) == 1

    # 互相封鎖：兩邊都看不到
    await s.execute(
        text(
            "UPDATE friendships SET mutual_block=true WHERE user_id=:a AND friend_id=:b"
        ),
        {"a": u["a"], "b": u["b"]},
    )
    assert (await _search(s, u["b"], "跟 b"))["messages"] == []


async def test_like_wildcards_are_literal(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    pct = await _dm(s, u["a"], u["b"], "勝率 100% 確定")
    await _dm(s, u["a"], u["b"], "勝率 1000 元")
    under = await _dm(s, u["a"], u["b"], "代號 a_b")
    await _dm(s, u["a"], u["b"], "代號 axb")
    slash = await _dm(s, u["a"], u["b"], r"路徑 c:\temp")
    await _dm(s, u["a"], u["b"], "路徑 c:temp")

    assert _ids((await _search(s, u["a"], "0%"))["messages"]) == [pct["id"]]
    assert _ids((await _search(s, u["a"], "a_b"))["messages"]) == [under["id"]]
    assert _ids((await _search(s, u["a"], ":\\"))["messages"]) == [slash["id"]]
    assert _ids((await _search(s, u["a"], "%"))["messages"]) == [pct["id"]]


# ── 群組訊息 ──────────────────────────────────────────────


async def test_group_messages_visibility(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, f"群{make.suffix}", "a", "b")
    before = await _gsend(s, gid, u["a"], "kw c 入群前")
    await _join(s, u, make, gid, "a", "c")  # 預設看不到入群前的訊息
    after = await _gsend(s, gid, u["b"], "kw c 入群後")
    recalled = await _gsend(s, gid, u["b"], "kw 收回")
    assert (
        await group_messages_repo.recall_message(recalled["id"], u["b"], session=s)
    )["success"]
    # 別的群（c 不在）
    other = await _group(s, u, make, f"別群{make.suffix}", "d", "e")
    await _gsend(s, other, u["d"], "kw 別群")

    assert _ids((await _search(s, u["c"], "kw"))["messages"]) == [after["id"]]
    assert set(_ids((await _search(s, u["a"], "kw"))["messages"])) == {
        before["id"],
        after["id"],
    }
    # 系統訊息的 content 是事件碼（member_joined:<uid>）：搜不到
    assert (await _search(s, u["a"], "member_joined"))["messages"] == []
    assert (await _search(s, u["a"], u["c"]))["messages"] == []
    # 不是成員：一則都沒有
    assert (await _search(s, u["f"], "kw"))["messages"] == []

    item = (await _search(s, u["c"], "入群後"))["messages"][0]
    assert item["kind"] == "group"
    assert item["group_id"] == gid and item["chat_name"] == f"群{make.suffix}"
    assert item["conversation_id"] is None and item["other_user_id"] is None
    assert item["from_user_id"] == u["b"] and item["from_name"] == u["b"]


async def test_messages_newest_first_merged_and_limited(gc_pg):
    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    gid = await _group(s, u, make, f"群{make.suffix}", "a", "b")
    dm_ids, group_ids = [], []
    # 私訊在偶數分鐘、群組在奇數分鐘，各 20 則
    for i in range(20):
        m = await _dm(s, u["b"], u["a"], f"kw 私訊 {i}")
        await _set_time(s, "dm_messages", m["id"], T0 + timedelta(minutes=2 * i))
        dm_ids.append(m["id"])
        g = await _gsend(s, gid, u["b"], f"kw 群組 {i}")
        await _set_time(s, "group_messages", g["id"], T0 + timedelta(minutes=2 * i + 1))
        group_ids.append(g["id"])

    res = (await _search(s, u["a"], "kw"))["messages"]
    assert len(res) == 30
    times = [datetime.fromisoformat(m["created_at"]) for m in res]
    assert times == sorted(times, reverse=True)
    assert times[0] == T0 + timedelta(minutes=39)
    # 交錯：最新是群組 19、再來私訊 19、群組 18…
    assert [(m["kind"], m["message_id"]) for m in res[:4]] == [
        ("group", group_ids[19]),
        ("dm", dm_ids[19]),
        ("group", group_ids[18]),
        ("dm", dm_ids[18]),
    ]


# ── 純函式 ──────────────────────────────────────────────


def test_like_pattern_escapes_wildcards():
    assert like_pattern("100%") == "%100\\%%"
    assert like_pattern("a_b") == "%a\\_b%"
    assert like_pattern("c:\\") == "%c:\\\\%"
    assert like_pattern("比特幣") == "%比特幣%"


def test_snippet_short_content_unchanged():
    assert make_snippet("BTC 要回測 6 萬", "回測") == "BTC 要回測 6 萬"


def test_snippet_cjk_middle_match():
    content = "前" * 50 + "比特幣" + "後" * 100
    snip = make_snippet(content, "比特幣")
    assert snip == "…" + "前" * 30 + "比特幣" + "後" * 60 + "…"


def test_snippet_match_at_start_and_end():
    content = "比特幣" + "後" * 100
    assert make_snippet(content, "比特幣") == "比特幣" + "後" * 60 + "…"
    content = "前" * 100 + "比特幣"
    assert make_snippet(content, "比特幣") == "…" + "前" * 30 + "比特幣"


def test_snippet_uses_first_of_multiple_matches_case_insensitive():
    content = "x" * 40 + "Btc 第一次" + "y" * 80 + "BTC 第二次"
    snip = make_snippet(content, "btc")
    assert snip.startswith("…" + "x" * 30 + "Btc 第一次")
    assert "第二次" not in snip and snip.endswith("…")


def test_snippet_never_exceeds_max_len():
    content = "前" * 200 + "比特幣" * 20 + "後" * 200
    query = "比特幣" * 20  # 60 字：前 30 + 命中 60 + 後 60 超過 100，後文被截
    snip = make_snippet(content, query)
    body = snip.strip("…")
    assert len(body) == 100
    assert query in body and snip.startswith("…") and snip.endswith("…")


def test_snippet_match_longer_than_window():
    content = "a" * 10 + "b" * 150 + "c" * 10
    snip = make_snippet(content, "b" * 150, max_len=100)
    assert snip == "…" + "b" * 100 + "…"


def test_snippet_flattens_newlines_and_falls_back_to_head():
    assert make_snippet("第一行\n第二行 kw", "kw") == "第一行 第二行 kw"
    # 找不到（理論上 DB 比對跟 re 不分大小寫規則有落差時）：從頭顯示
    assert make_snippet("沒有命中" + "字" * 100, "zzz") == "沒有命中" + "字" * 56 + "…"
