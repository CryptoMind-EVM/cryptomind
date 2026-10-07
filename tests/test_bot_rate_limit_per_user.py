"""Telegram bot 的限流要以 Telegram 使用者分開計（2026-09-24）。

bot 服務代使用者打 API 時不帶 JWT，限流 key 退回「來源 IP」＝bot 容器的一個 IP——
結果 bot_chat 的 10/min 是**全體 Telegram 使用者共用**，推廣一帶人進來就互相 429。

修法：X-Bot-Secret 驗過（hmac.compare_digest）才採信 bot 附上的 X-Telegram-User-Id，
key 改成 tg:<id>；祕密不對、沒設、或 id 不是純數字一律退回 IP（行為同舊版，不放寬）。
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from api.middleware import rate_limit
from bot.telegram_bot import ApiClient

pytestmark = pytest.mark.unit

SECRET = "s3cret-for-tests"


def _request(headers: dict[str, str], ip: str = "10.0.0.7") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/telegram/chat",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (ip, 12345),
        "query_string": b"",
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("BOT_INTERNAL_SECRET", SECRET)


def test_verified_bot_request_is_keyed_per_telegram_user():
    a = rate_limit.get_user_identifier(
        _request({"X-Bot-Secret": SECRET, "X-Telegram-User-Id": "111"})
    )
    b = rate_limit.get_user_identifier(
        _request({"X-Bot-Secret": SECRET, "X-Telegram-User-Id": "222"})
    )
    assert a == "tg:111"
    assert b == "tg:222"


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Telegram-User-Id": "111"},  # 沒帶祕密——任何人都能偽造 id，不採信
        {"X-Bot-Secret": "wrong", "X-Telegram-User-Id": "111"},
        {"X-Bot-Secret": SECRET, "X-Telegram-User-Id": "111; drop"},
        {"X-Bot-Secret": SECRET, "X-Telegram-User-Id": ""},
        {"X-Bot-Secret": SECRET},  # 舊版 bot（部署交接期）→ 維持舊行為
    ],
)
def test_unverified_or_malformed_falls_back_to_ip(headers):
    assert rate_limit.get_user_identifier(_request(headers)) == "ip:10.0.0.7"


def test_no_secret_configured_never_trusts_header(monkeypatch):
    monkeypatch.setenv("BOT_INTERNAL_SECRET", "")
    key = rate_limit.get_user_identifier(
        _request({"X-Bot-Secret": "", "X-Telegram-User-Id": "111"})
    )
    assert key == "ip:10.0.0.7"


def test_bot_client_sends_telegram_user_header():
    api = ApiClient("http://api", SECRET)
    assert api._headers(123) == {"X-Bot-Secret": SECRET, "X-Telegram-User-Id": "123"}
    assert api._headers() == {"X-Bot-Secret": SECRET}


def test_every_bot_post_forwards_telegram_id():
    """_post 從 payload 取 telegram_id 帶進標頭——所有 bot 端點的 payload 都有這個欄位。"""
    import inspect

    src = inspect.getsource(ApiClient._post)
    assert 'payload.get("telegram_id")' in src
    assert "self._headers(" in src
