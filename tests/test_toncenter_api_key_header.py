"""toncenter 金鑰只能走 X-API-Key header，不能出現在 URL。

以前金鑰放在 query string（?api_key=...）：httpx 在 INFO 會記一行
``HTTP Request: GET <完整 URL>``，例外訊息也可能帶 URL——金鑰就這樣進 log。
這裡用 httpx.MockTransport 攔下真正送出的 request，驗 URL、header 與 log。
（TON 付款驗證 api/ton_verification.py 已在 2026-09-26 移除，只剩 TON 餘額查詢工具。）
"""

from __future__ import annotations

import functools
import logging

import httpx
import pytest

import core.tools.crypto_modules.ton_balance as tb

pytestmark = pytest.mark.unit

KEY = "tc-secret-key-do-not-log-1234567890"
# 真地址：get_ton_balance 會解碼驗證（tag／CRC），"EQ" + "A"*46 這種會被擋
TON_ADDR = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _capture(body: dict):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    return seen, httpx.MockTransport(handler)


def _assert_key_only_in_header(seen, caplog):
    assert seen, "沒有送出任何 request"
    for req in seen:
        assert KEY not in str(req.url), f"金鑰出現在 URL：{req.url}"
        assert "api_key" not in req.url.params
        assert req.headers.get("X-API-Key") == KEY
    assert KEY not in caplog.text, "金鑰出現在 log"




@pytest.mark.asyncio
async def test_ton_balance_sends_key_as_header(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(tb, "TONCENTER_API_KEY", KEY)
    seen, transport = _capture(
        {"ok": True, "result": {"balance": "1000000000", "state": "active"}}
    )
    monkeypatch.setattr(
        tb.httpx,
        "AsyncClient",
        functools.partial(_REAL_ASYNC_CLIENT, transport=transport),
    )
    out = await tb.get_ton_balance.ainvoke({"address": TON_ADDR})
    assert out["balance_ton"] == 1.0
    _assert_key_only_in_header(seen, caplog)


@pytest.mark.asyncio
async def test_no_key_configured_sends_no_header(monkeypatch):
    monkeypatch.setattr(tb, "TONCENTER_API_KEY", "")
    seen, transport = _capture(
        {"ok": True, "result": {"balance": "1000000000", "state": "active"}}
    )
    monkeypatch.setattr(
        tb.httpx,
        "AsyncClient",
        functools.partial(_REAL_ASYNC_CLIENT, transport=transport),
    )
    await tb.get_ton_balance.ainvoke({"address": TON_ADDR})
    assert "X-API-Key" not in seen[0].headers
    assert "api_key" not in seen[0].url.params
