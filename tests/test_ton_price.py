"""TON 取價 fallback 鏈單元測試(CoinGecko 主源 + TonAPI 備援)。

覆蓋:
  - 主源 CoinGecko 成功 → 回價 + 寫 cache
  - CoinGecko 429/斷線/格式錯 → fallback TonAPI 成功
  - 兩源都失敗 → None(fail-closed)
  - cache 命中 → 不打網路
  - TonAPI 呼叫帶 Origin header(與既有 jetton_balances 同模式)

不打真實 API,全 mock httpx.get + shared_cache。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from core.tools.crypto_modules import ton_price
from core.tools.crypto_modules.ton_price import get_ton_usd_price


def _mock_response(status_code=200, json_body=None):
    """組 mock httpx.Response。"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    return resp


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch):
    """每個測試都從空 cache 開始(避免跨測試汙染)。"""
    monkeypatch.setattr(ton_price, "get_json", lambda _key: None)
    captured = {}

    def _set_json(_key, value, _ttl):
        captured["value"] = value

    monkeypatch.setattr(ton_price, "set_json", _set_json)
    yield captured


class TestPrimaryCoinGecko:
    def test_coingecko_success_returns_price(self, _isolate_cache):
        """CoinGecko 200 → 回價 + 寫 cache。"""
        coingecko_resp = _mock_response(
            200, {ton_price._TON_COINGECKO_ID: {"usd": 1.33}}
        )
        with patch("core.tools.crypto_modules.ton_price.httpx.get") as mock_get:
            mock_get.side_effect = [coingecko_resp]
            price = get_ton_usd_price()

        assert price == 1.33
        assert _isolate_cache["value"] == 1.33
        # 只打一次(CoinGecko 成功不 fallback)
        assert mock_get.call_count == 1

    def test_coingecko_zero_price_falls_back_to_tonapi(self, _isolate_cache):
        """CoinGecko 回 price=0(無效)→ fallback TonAPI。"""
        coingecko_resp = _mock_response(
            200, {ton_price._TON_COINGECKO_ID: {"usd": 0}}
        )
        tonapi_resp = _mock_response(200, {"rates": {"TON": {"prices": {"USD": 1.39}}}})
        with patch("core.tools.crypto_modules.ton_price.httpx.get") as mock_get:
            mock_get.side_effect = [coingecko_resp, tonapi_resp]
            price = get_ton_usd_price()

        # price=0 → CoinGecko 回 None → fallback TonAPI 成功
        assert price == 1.39


class TestFallbackTonApi:
    def test_fallback_when_coingecko_429(self, _isolate_cache):
        """CoinGecko 429 → TonAPI 200 → 回 TonAPI 價。"""
        coingecko_resp = _mock_response(429)
        tonapi_resp = _mock_response(200, {"rates": {"TON": {"prices": {"USD": 1.34}}}})
        with patch("core.tools.crypto_modules.ton_price.httpx.get") as mock_get:
            mock_get.side_effect = [coingecko_resp, tonapi_resp]
            price = get_ton_usd_price()

        assert price == 1.34
        assert _isolate_cache["value"] == 1.34
        assert mock_get.call_count == 2

    def test_fallback_when_coingecko_connection_error(self, _isolate_cache):
        """CoinGecko 斷線 → TonAPI 200 → 回 TonAPI 價。"""
        tonapi_resp = _mock_response(200, {"rates": {"TON": {"prices": {"USD": 1.35}}}})
        with patch("core.tools.crypto_modules.ton_price.httpx.get") as mock_get:
            mock_get.side_effect = [httpx.ConnectError("down"), tonapi_resp]
            price = get_ton_usd_price()

        assert price == 1.35

    def test_fallback_when_coingecko_bad_format(self, _isolate_cache):
        """CoinGecko 回格式錯 → TonAPI 200 → 回 TonAPI 價。"""
        coingecko_resp = _mock_response(200, {"unexpected": True})
        tonapi_resp = _mock_response(200, {"rates": {"TON": {"prices": {"USD": 1.36}}}})
        with patch("core.tools.crypto_modules.ton_price.httpx.get") as mock_get:
            mock_get.side_effect = [coingecko_resp, tonapi_resp]
            price = get_ton_usd_price()

        assert price == 1.36

    def test_tonapi_origin_header(self, _isolate_cache):
        """TonAPI 呼叫帶 Origin: https://tonapi.io(與既有 jetton_balances 同模式)。"""
        coingecko_resp = _mock_response(429)
        tonapi_resp = _mock_response(200, {"rates": {"TON": {"prices": {"USD": 1.37}}}})
        with patch("core.tools.crypto_modules.ton_price.httpx.get") as mock_get:
            mock_get.side_effect = [coingecko_resp, tonapi_resp]
            get_ton_usd_price()

        # 第二次呼叫(TonAPI)應帶 Origin header
        tonapi_call = mock_get.call_args_list[1]
        assert tonapi_call.kwargs.get("headers", {}).get("Origin") == "https://tonapi.io"


class TestBothFail:
    def test_both_fail_returns_none(self, _isolate_cache):
        """兩源都失敗 → None(fail-closed)。"""
        coingecko_resp = _mock_response(429)
        tonapi_resp = _mock_response(503)
        with patch("core.tools.crypto_modules.ton_price.httpx.get") as mock_get:
            mock_get.side_effect = [coingecko_resp, tonapi_resp]
            price = get_ton_usd_price()

        assert price is None
        # 不寫 cache
        assert "value" not in _isolate_cache

    def test_both_connection_error_returns_none(self, _isolate_cache):
        """兩源都斷線 → None。"""
        with patch("core.tools.crypto_modules.ton_price.httpx.get") as mock_get:
            mock_get.side_effect = [
                httpx.ConnectError("cg down"),
                httpx.ConnectError("tonapi down"),
            ]
            price = get_ton_usd_price()

        assert price is None


class TestCacheHit:
    def test_cache_hit_skips_network(self, monkeypatch):
        """cache 命中 → 不打網路。"""
        monkeypatch.setattr(ton_price, "get_json", lambda _key: 1.38)
        set_calls = []
        monkeypatch.setattr(
            "core.tools.crypto_modules.ton_price.set_json",
            lambda *a, **k: set_calls.append(a),
        )
        with patch("core.tools.crypto_modules.ton_price.httpx.get") as mock_get:
            price = get_ton_usd_price()

        assert price == 1.38
        mock_get.assert_not_called()  # 不打網路
        assert len(set_calls) == 0  # 不重寫 cache
