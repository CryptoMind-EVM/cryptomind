"""Bot HITL 橋（core/bot_hitl.py）＋ run_bot_chat 的 interrupt 路徑。

背景：Telegram 打「午餐 180」→ record_entry 提案 → consent interrupt →
bot 只讀 final_response → 回「無法產生回覆」。這裡守住：
1. 各種 interrupt payload 都能渲染成文字＋按鈕
2. decision → resume 答案的形狀對齊各 parser
3. run_bot_chat 遇到 __interrupt__ 回 hitl 而不是空回覆；resume 走 Command(resume)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agents.manager.claw_loop import parse_multi_consent_answers
from core.agents.manager.consent_gate import (
    parse_consent_answer,
    parse_skill_memory_consent_answer,
)
from core.bot_hitl import build_resume_answer, render_hitl, text_decision

pytestmark = pytest.mark.unit


# ── render_hitl ─────────────────────────────────────────────────────────────


def test_journal_consent_renders_amount_and_two_buttons():
    out = render_hitl(
        {
            "type": "journal_consent",
            "message": "Agent 提議記一筆支出",
            "entry_type": "expense",
            "amount": 180,
            "currency": "TWD",
            "category": "food",
            "note": "午餐",
        },
        "zh-TW",
    )
    assert out["type"] == "journal_consent"
    assert "180 TWD" in out["text"]
    assert "午餐" in out["text"]
    assert [b["decision"] for b in out["buttons"]] == ["approve", "deny"]
    assert "記下來" in out["buttons"][0]["label"]


def test_multi_consent_lists_every_card():
    out = render_hitl(
        {
            "type": "multi_consent",
            "message": "有 2 個提案",
            "cards": [
                {"icon": "📒", "title": "記帳", "lines": ["支出 · 180 TWD"]},
                {"icon": "🧠", "title": "記憶", "lines": ["[fact] 我住台北"]},
            ],
        },
        "en",
    )
    assert "180 TWD" in out["text"] and "我住台北" in out["text"]
    assert [b["decision"] for b in out["buttons"]] == ["approve", "deny"]


def test_clarify_options_become_option_buttons():
    out = render_hitl(
        {
            "type": "clarify",
            "question": "你指的是？",
            "options": [
                {"label": "大盤", "hint": "S&P 500"},
                {"label": "個股", "hint": "AAPL"},
            ],
        },
        "zh-TW",
    )
    assert out["buttons"][0] == {"label": "大盤", "decision": "option", "index": 0}
    assert out["buttons"][1]["index"] == 1


def test_loop_fork_offers_wrap_up():
    out = render_hitl(
        {"type": "loop_fork", "steps": 12, "question": "還要繼續嗎"}, "en"
    )
    assert out["buttons"] == [{"label": "🏁 Wrap up", "decision": "wrap"}]
    assert "12" in out["text"]


def test_unknown_payload_still_renders_something():
    out = render_hitl(None, "ru")
    assert out["type"] == "clarify"
    assert out["text"]


# ── build_resume_answer 對齊各 parser ────────────────────────────────────────


@pytest.mark.parametrize("ptype", ["consent_gate", "journal_consent", "memory_consent"])
def test_consent_answer_is_understood_by_both_parsers(ptype):
    yes = build_resume_answer({"type": ptype}, "approve")
    no = build_resume_answer({"type": ptype}, "deny")
    assert parse_consent_answer(yes)["approved"] is True
    assert parse_consent_answer(no)["approved"] is False
    assert parse_skill_memory_consent_answer(yes)["approved"] is True
    assert parse_skill_memory_consent_answer(no)["approved"] is False


def test_multi_consent_answer_has_one_entry_per_card():
    payload = {"type": "multi_consent", "cards": [{}, {}, {}]}
    yes = build_resume_answer(payload, "approve")
    parsed = parse_multi_consent_answers(yes, expected=3)
    assert [a["approved"] for a in parsed] == [True, True, True]
    no = parse_multi_consent_answers(build_resume_answer(payload, "deny"), expected=3)
    assert [a["approved"] for a in no] == [False, False, False]


def test_clarify_option_sends_hint_like_the_web_does():
    payload = {
        "type": "clarify",
        "options": [{"label": "大盤", "hint": "S&P 500 index"}],
    }
    assert build_resume_answer(payload, "option", option_index=0) == "S&P 500 index"
    assert build_resume_answer(payload, "option", option_index=9) == ""
    assert build_resume_answer(payload, "text", text="我要問台積電") == "我要問台積電"


def test_loop_fork_wrap_is_empty_string_and_hint_passes_through():
    assert build_resume_answer({"type": "loop_fork"}, "wrap") == ""
    assert (
        build_resume_answer({"type": "loop_fork"}, "text", text="只看技術面")
        == "只看技術面"
    )


# ── text_decision（打字回卡）─────────────────────────────────────────────────


def test_yes_no_words_answer_a_pending_consent():
    p = {"type": "journal_consent"}
    assert text_decision(p, "好") == {"decision": "approve"}
    assert text_decision(p, "OK") == {"decision": "approve"}
    assert text_decision(p, "不要。") == {"decision": "deny"}
    # 別的話＝新問題，不是回卡
    assert text_decision(p, "BTC 現在多少") is None


def test_clarify_accepts_number_or_free_text():
    p = {"type": "clarify", "options": [{"label": "a"}, {"label": "b"}]}
    assert text_decision(p, "2") == {"decision": "option", "option_index": 1}
    assert text_decision(p, "我想問大盤") == {"decision": "text", "text": "我想問大盤"}


# ── run_bot_chat／run_bot_resume ─────────────────────────────────────────────


class _Interrupt:
    def __init__(self, value):
        self.value = value


def _fake_manager(result: dict, pending_payload=None):
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value=result)
    interrupts = (_Interrupt(pending_payload),) if pending_payload is not None else ()
    graph.get_state = MagicMock(return_value=SimpleNamespace(interrupts=interrupts))
    return SimpleNamespace(graph=graph)


def _patch_pipeline(monkeypatch, manager):
    import api.routers.telegram_chat as mod

    monkeypatch.setattr(
        mod,
        "resolve_user_llm_credentials",
        AsyncMock(return_value={"provider": "openai", "api_key": "k", "model": "m"}),
    )
    monkeypatch.setattr(mod, "create_user_llm_client", lambda **_: object())
    monkeypatch.setattr(mod, "bootstrap", lambda **_: manager)
    saved = []
    monkeypatch.setattr(mod, "ensure_session", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_build_history_text", lambda *a, **k: "")
    monkeypatch.setattr(
        mod, "save_chat_message", lambda role, text, *a, **k: saved.append((role, text))
    )
    monkeypatch.setattr(mod, "set_current_session", lambda *a, **k: None)
    return saved


def test_run_bot_chat_returns_hitl_instead_of_empty_reply(monkeypatch):
    from api.routers.telegram_chat import run_bot_chat

    payload = {
        "type": "journal_consent",
        "entry_type": "expense",
        "amount": 180,
        "currency": "TWD",
    }
    manager = _fake_manager({"__interrupt__": [_Interrupt(payload)]})
    saved = _patch_pipeline(monkeypatch, manager)

    resp = asyncio.run(
        run_bot_chat(
            user_id="u1",
            session_id="tg:1",
            default_session_id="tg:1",
            default_session_title="Telegram Chat",
            message="午餐 180",
            language="zh-TW",
        )
    )
    assert resp.hitl is not None
    assert resp.hitl["type"] == "journal_consent"
    assert "180 TWD" in resp.hitl["text"]
    assert resp.response == resp.hitl["text"]
    # 卡片文字不當 assistant 訊息存進歷史（網頁版同樣不存）
    assert [r for r, _ in saved] == ["user"]


def test_run_bot_chat_yes_word_resumes_pending_consent(monkeypatch):
    """pending 記帳卡時打「好」→ Command(resume=…) 而不是開新問題。"""
    from langgraph.types import Command

    from api.routers.telegram_chat import run_bot_chat

    manager = _fake_manager(
        {"final_response": "已記錄 ✅"},
        pending_payload={"type": "journal_consent", "amount": 180},
    )
    saved = _patch_pipeline(monkeypatch, manager)

    resp = asyncio.run(
        run_bot_chat(
            user_id="u1",
            session_id="tg:1",
            default_session_id="tg:1",
            default_session_title="Telegram Chat",
            message="好",
            language="zh-TW",
        )
    )
    assert resp.response == "已記錄 ✅" and resp.hitl is None
    sent = manager.graph.ainvoke.await_args.args[0]
    assert isinstance(sent, Command)
    assert sent.resume == {"action": "consent", "approved": True}
    assert not sent.update
    assert ("assistant", "已記錄 ✅") in saved


def test_run_bot_chat_other_text_with_pending_consent_is_a_new_query(monkeypatch):
    from langgraph.types import Command

    from api.routers.telegram_chat import run_bot_chat

    manager = _fake_manager(
        {"final_response": "BTC 現價…"},
        pending_payload={"type": "journal_consent", "amount": 180},
    )
    _patch_pipeline(monkeypatch, manager)
    asyncio.run(
        run_bot_chat(
            user_id="u1",
            session_id="tg:1",
            default_session_id="tg:1",
            default_session_title="Telegram Chat",
            message="BTC 現在多少",
            language="zh-TW",
        )
    )
    sent = manager.graph.ainvoke.await_args.args[0]
    assert isinstance(sent, Command)
    assert sent.resume is None and sent.goto == "claw_loop"


def test_run_bot_resume_translates_decision_and_returns_final(monkeypatch):
    from langgraph.types import Command

    from api.routers.telegram_chat import run_bot_resume

    manager = _fake_manager(
        {"final_response": "已記錄 ✅"},
        pending_payload={"type": "multi_consent", "cards": [{}, {}]},
    )
    saved = _patch_pipeline(monkeypatch, manager)
    resp = asyncio.run(
        run_bot_resume(
            user_id="u1", session_id="tg:1", language="zh-TW", decision="deny"
        )
    )
    sent = manager.graph.ainvoke.await_args.args[0]
    assert isinstance(sent, Command)
    assert sent.resume == [{"action": "deny"}, {"action": "deny"}]
    assert resp.response == "已記錄 ✅"
    assert ("assistant", "已記錄 ✅") in saved


def test_run_bot_resume_without_pending_interrupt_is_409(monkeypatch):
    from fastapi import HTTPException

    from api.routers.telegram_chat import run_bot_resume

    manager = _fake_manager({"final_response": "x"}, pending_payload=None)
    _patch_pipeline(monkeypatch, manager)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            run_bot_resume(
                user_id="u1", session_id="tg:1", language="zh-TW", decision="approve"
            )
        )
    assert exc.value.status_code == 409
    manager.graph.ainvoke.assert_not_awaited()


# ── Telegram bot 端：按鈕鍵盤與 callback 解析 ───────────────────────────────


def test_hitl_keyboard_pairs_consent_buttons_and_lists_options():
    from bot.telegram_bot import _HITL_CB_PREFIX, _hitl_keyboard

    kb = _hitl_keyboard(
        {
            "buttons": [
                {"label": "✅ 記下來", "decision": "approve"},
                {"label": "❌ 不用", "decision": "deny"},
            ]
        }
    )
    assert [[b.callback_data for b in row] for row in kb.inline_keyboard] == [
        [f"{_HITL_CB_PREFIX}approve", f"{_HITL_CB_PREFIX}deny"]
    ]
    kb = _hitl_keyboard(
        {
            "buttons": [
                {"label": "大盤", "decision": "option", "index": 0},
                {"label": "個股", "decision": "option", "index": 1},
            ]
        }
    )
    assert [[b.callback_data for b in row] for row in kb.inline_keyboard] == [
        [f"{_HITL_CB_PREFIX}opt:0"],
        [f"{_HITL_CB_PREFIX}opt:1"],
    ]
    assert _hitl_keyboard(None) is None
    assert _hitl_keyboard({"buttons": []}) is None


def test_parse_hitl_callback_is_fail_closed():
    from bot.telegram_bot import _parse_hitl_callback

    assert _parse_hitl_callback("hitl:approve") == ("approve", None)
    assert _parse_hitl_callback("hitl:opt:2") == ("option", 2)
    assert _parse_hitl_callback("hitl:wrap") == ("wrap", None)
    assert _parse_hitl_callback("hitl:whatever") == ("deny", None)
    assert _parse_hitl_callback("hitl:opt:x") == ("deny", None)


def test_deliver_attaches_keyboard_only_for_hitl():
    from bot.telegram_bot import _deliver

    bot = MagicMock()
    bot.edit_message_text = AsyncMock()
    bot.send_message = AsyncMock()
    asyncio.run(
        _deliver(
            bot,
            1,
            2,
            {
                "response": "記一筆？",
                "hitl": {"buttons": [{"label": "✅", "decision": "approve"}]},
            },
            "zh-TW",
        )
    )
    kwargs = bot.edit_message_text.await_args.kwargs
    assert kwargs["text"] == "記一筆？" and kwargs["reply_markup"] is not None

    bot.edit_message_text.reset_mock()
    asyncio.run(_deliver(bot, 1, 2, {"response": "答案"}, "zh-TW"))
    assert bot.edit_message_text.await_args.kwargs["reply_markup"] is None
    # 一般回答會附上「非投資建議」聲明；確認卡（HITL）不附
    assert "不構成投資建議" in bot.edit_message_text.await_args.kwargs["text"]
