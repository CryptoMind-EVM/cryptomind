"""對話回覆語言（2026-09-28）：照這則訊息的語言；看不出來才用帳號語言。

另含 Telegram /lang（改帳號語言）的後端端點與 bot 接線。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.reply_language import detect_message_language, resolve_reply_language

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("BTC 現在價格多少？", "zh-TW"),
        ("BTC现在价格多少？", "zh-CN"),
        ("幫我分析 NVDA 最近的走勢", "zh-TW"),
        ("帮我分析 NVDA 最近的走势", "zh-CN"),
        ("What's the BTC price today?", "en"),
        ("btc price", "en"),
        ("Какая цена биткоина сегодня?", "ru"),
    ],
)
def test_clear_messages_are_detected(text, expected):
    assert detect_message_language(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "BTC?",
        "NVDA",
        "TSLA AAPL NVDA",
        "hi",
        "0x1234567890abcdef1234567890abcdef12345678",
        "12345.67",
        "ビットコインの価格は？",  # 日文：不支援，交給帳號語言
        "비트코인 가격",  # 韓文
        "",
        None,
    ],
)
def test_unclear_messages_fall_back(text):
    """只有代號、地址、數字、太短或不支援的語言 → 不切換（免得一句 "BTC?" 就換成英文）。"""
    assert detect_message_language(text) is None


def test_ambiguous_chinese_keeps_account_variant():
    # 「分析」簡繁同形：帳號是簡中就維持簡中，其他情況用繁中
    assert detect_message_language("分析", "zh-CN") == "zh-CN"
    assert detect_message_language("分析", "en") == "zh-TW"


def test_resolve_order_message_then_account_then_default():
    assert resolve_reply_language("What is going on with ETH?", "zh-TW") == "en"
    assert resolve_reply_language("ETH?", "ru") == "ru"
    assert resolve_reply_language("ETH?", None, default="en") == "en"
    assert resolve_reply_language("ETH?", None) == "zh-TW"


def test_all_chat_entry_points_use_it():
    for rel in (
        "api/routers/analysis.py",
        "api/routers/telegram_chat.py",
        "api/routers/line_link.py",
    ):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "resolve_reply_language(" in src, rel


class TestTelegramLang:
    def _client(self):
        from api.routers import telegram_link as tl

        app = FastAPI()
        app.include_router(tl.router)
        app.dependency_overrides[tl._require_bot_secret] = lambda: None
        return TestClient(app), tl

    def test_sets_account_language(self):
        client, tl = self._client()
        with (
            patch.object(
                tl,
                "get_binding_by_telegram_id",
                return_value={"telegram_id": 1, "user_id": "u1"},
            ),
            patch("core.database.user.set_user_language", return_value=True) as setter,
        ):
            res = client.post(
                "/api/telegram/language", json={"telegram_id": 1, "language": "en"}
            )
        assert res.status_code == 200 and res.json()["language"] == "en"
        setter.assert_called_once_with("u1", "en")

    def test_rejects_unsupported_and_unbound(self):
        client, tl = self._client()
        with patch("core.database.user.set_user_language") as setter:
            res = client.post(
                "/api/telegram/language", json={"telegram_id": 1, "language": "ja"}
            )
            assert res.status_code == 422
            with patch.object(tl, "get_binding_by_telegram_id", return_value=None):
                res = client.post(
                    "/api/telegram/language", json={"telegram_id": 1, "language": "ru"}
                )
            assert res.status_code == 403
        setter.assert_not_called()


def test_bot_lang_command_wiring():
    bot = (REPO / "bot" / "telegram_bot.py").read_text(encoding="utf-8")
    assert '@router.message(Command("lang"))' in bot
    assert "F.data.startswith(_LANG_CB_PREFIX)" in bot
    assert '"/api/telegram/language"' in bot
    assert 'BotCommand(command="lang"' in bot
    from bot.i18n import MESSAGES

    for lang in ("zh-TW", "en"):
        assert "{name}" in MESSAGES[lang]["lang_set"]
        assert MESSAGES[lang]["lang_choose"]
        assert (
            "/lang" in MESSAGES[lang]["help"] and "/lang" in MESSAGES[lang]["welcome"]
        )


def test_short_follow_up_keeps_the_conversation_language():
    """英文問完接著只打 "and ETH?"：沿用同一對話上一則的語言，不跳回帳號語言。"""
    key = "test:conv-sticky"
    assert (
        resolve_reply_language("What about BTC today?", "zh-TW", conversation_key=key)
        == "en"
    )
    assert resolve_reply_language("and ETH?", "zh-TW", conversation_key=key) == "en"
    # 別的對話不受影響
    assert (
        resolve_reply_language("and ETH?", "zh-TW", conversation_key="test:other")
        == "zh-TW"
    )
    # 同一對話改用中文問，就跟著換
    assert (
        resolve_reply_language("那 SOL 呢？", "zh-TW", conversation_key=key) == "zh-TW"
    )
