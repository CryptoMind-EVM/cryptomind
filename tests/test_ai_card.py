"""AI 分析卡片（message_type = 'ai_card'，core/ai_card.py、c071）。

聊天室 AI 助理的回答「分享到聊天室」改成送出一則卡片：內容由伺服器依問答 id 取（不接受呼叫端傳來的文字），
不受一般訊息 500 字限制；列表預覽、通知、回覆引用只給一行。
純邏輯不連資料庫；repo 的部分用真 PostgreSQL、整段交易最後 rollback（同 test_chat_assistant_history）。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from core.ai_card import CARD_MAX_LENGTH, CARD_TYPE, card_preview
from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）

REPO = Path(__file__).resolve().parents[1]

LONG_ANSWER = (
    "## BTC 重點\n\n"
    + "| 指標 | 數值 |\n|---|---|\n| RSI | 72 |\n\n"
    + "短線偏多，但要留意回檔。" * 80
)


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ── 純邏輯 ──────────────────────────────────────────────


def test_card_preview_is_one_clean_line():
    assert card_preview("## 📊 BTC **重點**\n\n短線偏多") == "✨ 📊 BTC 重點"
    # 表格的分隔列不算內容；HTML、清單符號、引用符號都拿掉
    assert card_preview("|---|---|\n| 指標 | 數值 |") == "✨ 指標 數值"
    assert card_preview("<p>你好<br>世界</p>") == "✨ 你好"
    assert card_preview("> - 1. 引用裡的清單") == "✨ 引用裡的清單"
    assert card_preview("") == "✨"
    assert card_preview(None) == "✨"
    # 超過上限截斷加 …，總長不超過 limit
    long = card_preview("字" * 500, limit=50)
    assert len(long) == 50 and long.endswith("…")
    assert len(card_preview("字" * 500, limit=100)) == 100


def test_reply_preview_of_a_card_is_one_line():
    from core.dm_reply import build_reply_preview

    preview = build_reply_preview(5, "u1", "小明", "xm", LONG_ANSWER, CARD_TYPE)
    assert preview["snippet"] == "✨ BTC 重點"
    assert preview["recalled"] is False
    # 一般訊息照舊：前 100 字
    text = build_reply_preview(5, "u1", "小明", "xm", "字" * 300, "text")
    assert len(text["snippet"]) == 100


def test_request_models_need_content_or_a_turn_id():
    from api.routers.group_chat import SendGroupMessageRequest
    from api.routers.messages import SendMessageRequest

    for model, extra in (
        (SendMessageRequest, {"to_user_id": "u"}),
        (SendGroupMessageRequest, {}),
    ):
        with pytest.raises(ValidationError):
            model(**extra)  # 兩個都沒有
        with pytest.raises(ValidationError):
            model(content="", **extra)
        assert model(content="hi", **extra).assistant_turn_id is None
        assert model(assistant_turn_id=3, **extra).content == ""  # 卡片不用帶文字
        with pytest.raises(ValidationError):
            model(assistant_turn_id=0, **extra)
        with pytest.raises(ValidationError):
            model(content="字" * 2001, **extra)  # 一般訊息的上限照舊


def test_migration_schema_in_sync():
    src = _read("alembic/versions/c071_group_messages_ai_card.py")
    assert re.search(r'^revision = "c071"$', src, re.M)
    assert re.search(r'^down_revision = "c070"$', src, re.M)
    upgrade, downgrade = src.split("def downgrade")
    assert "'text', 'recalled', 'system', 'ai_card'" in upgrade
    # 降版先把卡片改回 text（內容保留）才收緊約束，不然已有卡片時降版會失敗
    assert "SET message_type = 'text' WHERE message_type = 'ai_card'" in downgrade
    assert downgrade.index("SET message_type = 'text'") < downgrade.index(
        "ADD CONSTRAINT"
    )
    assert "'text', 'recalled', 'system')" in downgrade

    schema = _read("core/database/schema.py")
    assert "CHECK (message_type IN ('text', 'recalled', 'system', 'ai_card'))" in schema
    # 只有群組訊息表有這個約束；私訊表 message_type 本來就沒有 CHECK
    assert "CHECK (message_type IN ('text', 'greeting'" not in schema


def test_card_type_matches_frontend():
    assert CARD_TYPE == "ai_card"
    assert f"const AI_CARD_TYPE = '{CARD_TYPE}';" in _read("web/js/ai-card.js")


# ── 路由：內容一律由伺服器取 ─────────────────────────────


def _req():
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )


@pytest.fixture
def group_router(monkeypatch):
    from api.routers import group_chat as router

    async def fake_push(user_id, payload):
        return None

    async def fake_notify_push(user_id, notification):
        return None

    monkeypatch.setattr(router.message_manager, "send_to_user", fake_push)
    monkeypatch.setattr(router, "push_notification_to_user", fake_notify_push)
    monkeypatch.setattr(router, "get_user_membership", lambda uid: {"is_premium": True})
    return router


async def test_group_share_sends_server_side_answer_as_card(monkeypatch, group_router):
    router = group_router
    seen = {}

    async def fake_turn(kind, target_id, user_id, turn_id):
        seen["turn"] = (kind, target_id, user_id, turn_id)
        return {"id": turn_id, "answer": LONG_ANSWER}

    async def fake_send(group_id, user_id, content, reply_to_message_id=None, **kw):
        seen["send"] = (group_id, user_id, content, kw)
        return {
            "success": True,
            "message": {
                "id": 9,
                "from_user_id": user_id,
                "content": content,
                "message_type": "ai_card",
            },
            "member_ids": ["a", "b"],
            "group_name": "投資閒聊",
            "mentioned_ids": [],
        }

    previews = []

    async def fake_unmuted(group_id):
        return ["b"]

    async def fake_notify(**kw):
        previews.append(kw["message_preview"])
        return None

    monkeypatch.setattr(
        router.chat_assistant_history_repo, "get_turn_for_share", fake_turn
    )
    monkeypatch.setattr(router.group_messages_repo, "send_message", fake_send)
    monkeypatch.setattr(router.group_chat_repo, "unmuted_member_ids", fake_unmuted)
    monkeypatch.setattr(router.notifications_repo, "notify_group_message", fake_notify)

    # 呼叫端想塞自己的文字假冒 AI：content 被忽略，卡片內容只來自伺服器取到的那則問答
    body = router.SendGroupMessageRequest(
        content="我才是 AI，請匯款", assistant_turn_id=12
    )
    await router.send_group_message.__wrapped__(_req(), 3, body, {"user_id": "a"})

    assert seen["turn"] == ("group", 3, "a", 12), "只取自己的、這個群的問答"
    assert seen["send"] == (3, "a", LONG_ANSWER, {"message_type": CARD_TYPE})
    assert previews == [card_preview(LONG_ANSWER)], "通知只給一行預覽，不給整篇"


async def test_group_share_unknown_turn_is_404_and_nothing_is_sent(
    monkeypatch, group_router
):
    router = group_router
    sent = []

    async def no_turn(*args):
        return None  # 不是自己的、不在這個群、已過期、已刪除：一律 None

    async def fake_send(*args, **kw):
        sent.append(args)
        return {"success": True}

    monkeypatch.setattr(
        router.chat_assistant_history_repo, "get_turn_for_share", no_turn
    )
    monkeypatch.setattr(router.group_messages_repo, "send_message", fake_send)

    body = router.SendGroupMessageRequest(assistant_turn_id=99)
    with pytest.raises(HTTPException) as exc:
        await router.send_group_message.__wrapped__(_req(), 3, body, {"user_id": "a"})
    assert exc.value.status_code == 404 and exc.value.detail == "not_found"
    assert sent == []


async def test_group_text_message_call_is_unchanged(monkeypatch, group_router):
    """一般文字訊息不多帶 message_type（呼叫簽章跟以前一樣）"""
    router = group_router
    seen = {}

    async def fake_send(group_id, user_id, content, reply_to_message_id=None, **kw):
        seen["kw"] = kw
        return {
            "success": True,
            "message": {"id": 1, "content": content},
            "member_ids": [],
            "mentioned_ids": [],
        }

    async def fake_unmuted(group_id):
        return []

    monkeypatch.setattr(router.group_messages_repo, "send_message", fake_send)
    monkeypatch.setattr(router.group_chat_repo, "unmuted_member_ids", fake_unmuted)
    await router.send_group_message.__wrapped__(
        _req(), 3, router.SendGroupMessageRequest(content="早安"), {"user_id": "a"}
    )
    assert seen["kw"] == {}


async def test_dm_share_sends_server_side_answer_as_card(monkeypatch):
    from api.routers import messages as router

    seen = {}

    async def fake_valid(a, b):
        return {"valid": True}

    async def fake_conv(a, b):
        seen["pair"] = (a, b)
        return 41

    async def fake_turn(kind, target_id, user_id, turn_id):
        seen["turn"] = (kind, target_id, user_id, turn_id)
        return {"id": turn_id, "answer": LONG_ANSWER}

    async def fake_send(from_id, to_id, content, **kw):
        seen["send"] = (from_id, to_id, content, kw)
        return {
            "success": True,
            "message": {
                "id": 5,
                "conversation_id": 41,
                "from_user_id": from_id,
                "from_username": "xm",
                "content": content,
                "message_type": "ai_card",
            },
        }

    previews = []

    async def fake_notify(
        to_user_id,
        from_user_id,
        from_username,
        message_preview,
        conversation_id,
        message_id=None,
    ):
        previews.append(message_preview)
        return None

    async def fake_ws(user_id, payload):
        return None

    async def fake_push(user_id, notification):
        return None

    async def fake_sync(fn, *args):
        return fn(*args)

    monkeypatch.setattr(router.messages_repo, "validate_message_send", fake_valid)
    monkeypatch.setattr(
        router.chat_assistant_history_repo, "dm_conversation_id", fake_conv
    )
    monkeypatch.setattr(
        router.chat_assistant_history_repo, "get_turn_for_share", fake_turn
    )
    monkeypatch.setattr(router.messages_repo, "send_message", fake_send)
    monkeypatch.setattr(router.notifications_repo, "notify_new_message", fake_notify)
    monkeypatch.setattr(router.message_manager, "send_to_user", fake_ws)
    monkeypatch.setattr(router, "push_notification_to_user", fake_push)
    monkeypatch.setattr(router, "run_sync", fake_sync)
    monkeypatch.setattr(router, "get_user_membership", lambda uid: {"is_premium": True})
    monkeypatch.setattr(
        router,
        "check_and_increment_message",
        lambda uid, premium: {"can_send": True, "limit": 50},
    )

    body = router.SendMessageRequest(
        to_user_id="u2", content="假冒的內容", assistant_turn_id=7
    )
    result = await router.send_message_endpoint.__wrapped__(
        _req(), body, {"user_id": "u1"}
    )

    assert result["success"] is True
    assert seen["pair"] == ("u1", "u2")
    assert seen["turn"] == ("dm", 41, "u1", 7), (
        "歷史表的 target_id 是對話 id，不是對方的 user_id"
    )
    assert seen["send"] == (
        "u1",
        "u2",
        LONG_ANSWER,
        {"reply_to_message_id": None, "message_type": CARD_TYPE},
    )
    assert previews == [card_preview(LONG_ANSWER)]

    # 問答不屬於這個對話（拿不到）：404，而且不能扣到今日額度
    quota = []

    async def no_turn(*args):
        return None

    monkeypatch.setattr(
        router.chat_assistant_history_repo, "get_turn_for_share", no_turn
    )
    monkeypatch.setattr(
        router,
        "check_and_increment_message",
        lambda uid, premium: quota.append(1) or {"can_send": True, "limit": 50},
    )
    with pytest.raises(HTTPException) as exc:
        await router.send_message_endpoint.__wrapped__(
            _req(),
            router.SendMessageRequest(to_user_id="u2", assistant_turn_id=8),
            {"user_id": "u1"},
        )
    assert exc.value.status_code == 404
    assert quota == [], "驗不過就不扣額度"


# ── repo（真 PG）──────────────────────────────────────────


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


async def _save(s, kind, target, uid, answer=LONG_ANSWER, **kw):
    from core.orm.chat_assistant_history_repo import chat_assistant_history_repo

    row = await chat_assistant_history_repo.save_turn(
        kind, target, uid, "問", answer, [], {"model": "Lite"}, session=s, **kw
    )
    assert row is not None
    return row["id"]


async def test_get_turn_for_share_only_for_the_asker_in_that_chat(gc_pg):
    from core.orm.chat_assistant_history_repo import chat_assistant_history_repo as repo
    from core.orm.group_chat_repo import group_chat_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, "b")
    other_gid = await _group(s, u, make)
    turn_id = await _save(s, "group", gid, u["a"])

    got = await repo.get_turn_for_share("group", gid, u["a"], turn_id, session=s)
    assert got == {"id": turn_id, "answer": LONG_ANSWER}
    # 別人（同群的 b）不能分享 a 的問答；別的聊天室、別的種類也不行
    assert (
        await repo.get_turn_for_share("group", gid, u["b"], turn_id, session=s) is None
    )
    assert (
        await repo.get_turn_for_share("group", other_gid, u["a"], turn_id, session=s)
        is None
    )
    assert await repo.get_turn_for_share("dm", gid, u["a"], turn_id, session=s) is None
    assert (
        await repo.get_turn_for_share("group", gid, u["a"], turn_id + 9999, session=s)
        is None
    )
    # 過了保存期限（7 天）就當不存在
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert (
        await repo.get_turn_for_share(
            "group", gid, u["a"], turn_id, now=later, session=s
        )
        is None
    )
    # 退群後看不到這個聊天室：不能再分享
    await group_chat_repo.leave_group(gid, u["b"], session=s)
    turn_b = await _save(s, "group", other_gid, u["a"])
    assert await repo.get_turn_for_share(
        "group", other_gid, u["a"], turn_b, session=s
    ) == {
        "id": turn_b,
        "answer": LONG_ANSWER,
    }


async def test_get_turn_for_share_caps_length_and_dm_conversation_id(gc_pg):
    from core.orm.chat_assistant_history_repo import chat_assistant_history_repo as repo
    from core.orm.messages_repo import messages_repo

    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    sent = await messages_repo.send_message(u["a"], u["b"], "hi", session=s)
    conv = sent["message"]["conversation_id"]
    # 兩個方向查到同一個對話 id；沒有對話是 None
    assert await repo.dm_conversation_id(u["a"], u["b"], session=s) == conv
    assert await repo.dm_conversation_id(u["b"], u["a"], session=s) == conv
    assert await repo.dm_conversation_id(u["a"], u["c"], session=s) is None

    turn_id = await _save(s, "dm", conv, u["a"], answer="字" * (CARD_MAX_LENGTH + 500))
    got = await repo.get_turn_for_share("dm", conv, u["a"], turn_id, session=s)
    assert len(got["answer"]) == CARD_MAX_LENGTH, "保險上限"
    assert await repo.get_turn_for_share("dm", conv, u["b"], turn_id, session=s) is None


async def test_dm_card_is_exempt_from_the_500_char_limit(gc_pg):
    from core.orm.messages_repo import messages_repo

    s, u, make = gc_pg
    await make.friends(u["a"], u["b"])
    # 一般訊息：還是 500 字上限
    too_long = await messages_repo.send_message(u["a"], u["b"], "字" * 501, session=s)
    assert too_long == {
        "success": False,
        "error": "message_too_long",
        "max_length": 500,
    }
    # 卡片：不受 500 字管，但有自己的上限
    card = await messages_repo.send_message(
        u["a"], u["b"], LONG_ANSWER, message_type=CARD_TYPE, session=s
    )
    assert card["success"] is True and len(LONG_ANSWER) > 500
    assert card["message"]["message_type"] == CARD_TYPE
    assert card["message"]["content"] == LONG_ANSWER.strip()
    over = await messages_repo.send_message(
        u["a"], u["b"], "字" * (CARD_MAX_LENGTH + 1), message_type=CARD_TYPE, session=s
    )
    assert over["error"] == "message_too_long" and over["max_length"] == CARD_MAX_LENGTH

    # 列表預覽只給一行；對話內容（給卡片渲染）是整篇
    convs = await messages_repo.get_conversations(u["b"], session=s)
    assert convs[0]["last_message_type"] == CARD_TYPE
    assert convs[0]["last_message"] == card_preview(LONG_ANSWER, 100)
    page = await messages_repo.get_messages(
        card["message"]["conversation_id"], u["b"], session=s
    )
    assert page["messages"][-1]["content"] == LONG_ANSWER.strip()

    # 回覆卡片：引用只有一行
    reply = await messages_repo.send_message(
        u["b"], u["a"], "收到", reply_to_message_id=card["message"]["id"], session=s
    )
    assert reply["message"]["reply_to"]["snippet"] == "✨ BTC 重點"

    # 收回卡片：原文清空、類型變 recalled，跟一般訊息一樣
    recalled = await messages_repo.recall_message(
        card["message"]["id"], u["a"], session=s
    )
    assert recalled["success"] is True
    page = await messages_repo.get_messages(
        card["message"]["conversation_id"], u["b"], session=s
    )
    gone = next(m for m in page["messages"] if m["id"] == card["message"]["id"])
    assert gone["message_type"] == "recalled" and gone["content"] == ""


async def test_group_card_is_exempt_from_the_500_char_limit(gc_pg):
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, "b")
    too_long = await group_messages_repo.send_message(
        gid, u["a"], "字" * 501, session=s
    )
    assert too_long["error"] == "message_too_long"

    # 卡片裡面有 @成員 也不會變成提及（不通知別人）
    answer = LONG_ANSWER + f"\n@{u['b']}"
    card = await group_messages_repo.send_message(
        gid, u["a"], answer, message_type=CARD_TYPE, session=s
    )
    assert card["success"] is True
    assert card["message"]["message_type"] == CARD_TYPE
    assert card["message"]["mentions"] == [] and card["mentioned_ids"] == []
    over = await group_messages_repo.send_message(
        gid, u["a"], "字" * (CARD_MAX_LENGTH + 1), message_type=CARD_TYPE, session=s
    )
    assert over["error"] == "message_too_long"

    # 資料庫的 CHECK 認得 ai_card（c071）；亂填的類型還是會被擋
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with s.begin_nested():
            await s.execute(
                text(
                    "INSERT INTO group_messages (group_id, from_user_id, content, message_type) VALUES (:g, :u, 'x', 'bogus')"
                ),
                {"g": gid, "u": u["a"]},
            )

    # 列表預覽一行；讀取訊息是整篇
    groups = await group_chat_repo.list_groups(u["b"], session=s)
    assert groups[0]["last_message"]["message_type"] == CARD_TYPE
    assert groups[0]["last_message"]["content"] == card_preview(answer, 100)
    page = await group_messages_repo.get_messages(gid, u["b"], session=s)
    assert page["messages"][-1]["content"] == answer.strip()

    # 收回卡片：跟一般訊息一樣清空
    recalled = await group_messages_repo.recall_message(
        card["message"]["id"], u["a"], session=s
    )
    assert recalled["success"] is True


async def test_assistant_does_not_read_ai_cards_back(gc_pg):
    """卡片是 AI 自己的輸出：「整理最近的對話」不把它再餵給 AI"""
    from core.orm.chat_assistant_repo import chat_assistant_repo
    from core.orm.group_messages_repo import group_messages_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, "b")
    await group_messages_repo.send_message(gid, u["b"], "大家怎麼看 BTC", session=s)
    await group_messages_repo.send_message(
        gid, u["a"], LONG_ANSWER, message_type=CARD_TYPE, session=s
    )
    await group_messages_repo.send_message(gid, u["b"], "我覺得會回檔", session=s)

    result = await chat_assistant_repo.fetch_messages(
        "group", gid, u["a"], "recent", session=s
    )
    texts = [line["text"] for line in result["messages"]]
    assert "大家怎麼看 BTC" in texts and "我覺得會回檔" in texts
    assert not any("BTC 重點" in t for t in texts)


async def test_search_finds_card_text_in_groups_and_dms(gc_pg):
    from core.orm.chat_search_repo import chat_search_repo
    from core.orm.group_messages_repo import group_messages_repo
    from core.orm.messages_repo import messages_repo

    s, u, make = gc_pg
    gid = await _group(s, u, make, "b")  # 建群時 a、b 已經是好友
    answer = "## 卡片關鍵字zzq\n\n內容"
    await group_messages_repo.send_message(
        gid, u["a"], answer, message_type=CARD_TYPE, session=s
    )
    await messages_repo.send_message(
        u["a"], u["b"], answer, message_type=CARD_TYPE, session=s
    )

    found = await chat_search_repo.search(u["b"], "關鍵字zzq", session=s)
    kinds = sorted(m["kind"] for m in found["messages"])
    assert kinds == ["dm", "group"]
