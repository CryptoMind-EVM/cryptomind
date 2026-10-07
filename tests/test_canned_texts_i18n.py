"""
訪客與 Telegram/LINE bot 的罐頭文字依語言輸出（L3）。

- guest：「Not financial advice.」不能無條件附加（閒聊也附）；單次回答的空回應
  不能寫死英文「(empty response)」。
- telegram_chat：空回應與截斷提示不能寫死中文。
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routers import guest as guest_router
from core.i18n import t

pytestmark = pytest.mark.unit

_LANGUAGES = ["zh-TW", "zh-CN", "en", "ru"]
_CJK_RE = re.compile(r"[一-鿿]")


# ── guest ───────────────────────────────────────────────────────────────────


def test_guest_disclaimer_rule_is_conditional_on_market_content():
    rules = guest_router._GUEST_RULES
    assert "Not financial advice" in rules
    # 只在涉及市場／投資分析時附加；閒聊、問候不附
    rule4 = next(line for line in rules.splitlines() if line.startswith("4."))
    assert re.search(r"\bonly\b|\bif\b", rule4, re.IGNORECASE)
    assert re.search(r"small talk|greeting", rule4, re.IGNORECASE)
    assert "language of your answer" in rule4  # 依語言輸出維持不變


def test_guest_agent_and_basic_prompts_share_the_conditional_rule():
    assert guest_router._GUEST_RULES in guest_router._GUEST_AGENT_RULES
    assert guest_router._GUEST_RULES in guest_router._GUEST_SYSTEM_PROMPT


@pytest.mark.parametrize("language", _LANGUAGES)
async def test_guest_basic_answer_empty_reply_is_localized(monkeypatch, language):
    async def _snap():
        return ""

    async def _invoke(messages, short_id, deadline):
        return SimpleNamespace(content="   "), "provider/model"

    monkeypatch.setattr(guest_router, "_safe_snapshot", _snap)
    monkeypatch.setattr(guest_router, "_invoke_with_model_fallback", _invoke)

    text, used = await guest_router._basic_answer("你好", [], language, "abc12345", 0.0)

    assert text == t("errors.analysis.no_response", language)
    assert "(empty response)" not in text
    assert used == "basic:provider/model"


# ── telegram_chat（Telegram 與 LINE 共用 run_bot_chat 管線）──────────────────


def _graph_manager(final_response: str):
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"final_response": final_response})
    return SimpleNamespace(graph=graph)


def _quiet_persistence(monkeypatch):
    import api.routers.telegram_chat as mod

    monkeypatch.setattr(mod, "save_chat_message", lambda *a, **k: None)
    monkeypatch.setattr(mod, "set_current_session", lambda *a, **k: None)
    return mod


@pytest.mark.parametrize("language", _LANGUAGES)
async def test_bot_empty_final_response_follows_user_language(monkeypatch, language):
    mod = _quiet_persistence(monkeypatch)

    resp = await mod._run_graph_and_deliver(
        _graph_manager(""),
        object(),
        user_id="u1",
        session_id="tg:1",
        language=language,
        max_response_chars=mod.MAX_RESPONSE_CHARS,
    )

    assert resp.response == t("errors.analysis.no_response", language)


@pytest.mark.parametrize("language", _LANGUAGES)
async def test_bot_truncation_notice_follows_user_language(monkeypatch, language):
    mod = _quiet_persistence(monkeypatch)

    resp = await mod._run_graph_and_deliver(
        _graph_manager("x" * 50),
        object(),
        user_id="u1",
        session_id="tg:1",
        language=language,
        max_response_chars=10,
    )

    assert resp.response.startswith("x" * 10)
    notice = resp.response[10:].strip()
    assert notice
    if language in ("en", "ru"):
        assert not _CJK_RE.search(notice)
    if language == "zh-CN":
        assert "已截断" in notice
    if language == "zh-TW":
        assert "已截斷" in notice
