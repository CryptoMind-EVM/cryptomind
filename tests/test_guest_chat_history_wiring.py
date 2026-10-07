"""訪客多輪＋第一次回答後小卡的前端接線（上市準備 PR-4）。

- 訪客送出時帶本頁最近 6 則對話（上限與後端 Pydantic 驗證一致，超過會 422）
- 「新對話」重畫 #chat-messages 後，舊泡泡離開 DOM → 歷史跟著丟（不動 chat-sessions.js）
- 小卡只出現一次、登入鈕走 click-delegator（CSP 禁 inline handler）、四語都有字
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from api.routers import guest as guest_router

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "web/js/chat-analysis.js").read_text(encoding="utf-8")
DELEGATOR = (ROOT / "web/js/click-delegator.js").read_text(encoding="utf-8")
LOCALES = ("zh-TW", "zh-CN", "en", "ru")


def _const(name: str) -> int:
    m = re.search(rf"const {name} = (\d+);", JS)
    assert m, f"chat-analysis.js 找不到 {name}"
    return int(m.group(1))


def _fn(name: str) -> str:
    start = JS.index(f"function {name}(")
    end = JS.index("\n}\n", start)
    return JS[start:end]


def test_history_limits_match_backend_validation():
    assert _const("GUEST_HISTORY_MAX") == guest_router._HISTORY_MAX_ITEMS
    assert _const("GUEST_HISTORY_CHARS") == guest_router._HISTORY_CONTENT_MAX


def test_guest_request_carries_history():
    body = _fn("sendGuestMessage")
    assert "history: _guestHistoryPayload()" in body


def test_history_follows_chat_dom():
    """歷史綁著 bot 泡泡：新對話清掉畫面就等於清掉歷史。"""
    payload = _fn("_guestHistoryPayload")
    assert "isConnected" in payload
    assert "slice(-GUEST_HISTORY_MAX)" in payload


def test_only_successful_answers_enter_history():
    body = _fn("sendGuestMessage")
    push = body.index("guestTurns.push(")
    # 錯誤分支都在 push 之前 return 掉
    assert body.index("resp.status === 429") < push
    assert body.index("if (!resp.ok)") < push


def test_soft_cta_once_and_delegated():
    body = _fn("sendGuestMessage")
    assert "guestSoftCtaShown" in body
    card = _fn("_appendGuestSoftCta")
    assert 'data-click="openGuestSignIn"' in card
    assert "onclick" not in card.lower()
    assert "escapeHtml(" in card
    assert "window.openGuestSignIn = openGuestSignIn" in JS
    assert "'openGuestSignIn'" in DELEGATOR


@pytest.mark.parametrize("loc", LOCALES)
def test_soft_cta_copy_in_every_locale(loc):
    chat = json.loads(
        (ROOT / f"web/js/i18n/{loc}.json").read_text(encoding="utf-8")
    )["chat"]
    for key in ("guestSoftCtaText", "guestSoftCtaButton"):
        assert chat.get(key, "").strip(), f"{loc} 缺 chat.{key}"
    # 小卡不催連錢包
    assert "wallet" not in chat["guestSoftCtaText"].lower()


@pytest.mark.parametrize("loc", LOCALES)
def test_soft_cta_offers_brief_and_alerts_without_trade_directives(loc):
    """2026-10-05：首答後小卡改成馬上有用的鉤子（早報、價格提醒），且措辭只講資料與通知，
    不含買賣指令（analysis_not_advice）。"""
    text = json.loads((ROOT / f"web/js/i18n/{loc}.json").read_text(encoding="utf-8"))[
        "chat"
    ]["guestSoftCtaText"].lower()
    assert any(
        w in text for w in ("早報", "早报", "morning brief", "утреннюю сводку")
    ), loc
    assert any(w in text for w in ("提醒", "通知", "alert", "уведомление")), loc
    for banned in ("買", "买", "賣", "卖", "buy", "sell", "купи", "прода"):
        assert banned not in text, f"{loc} 小卡不能出現買賣字眼：{banned}"
