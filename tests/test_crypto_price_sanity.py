"""Tests for crypto price data-quality guards (C-1/C-2/C-3).

Regression: BTC 在線上回傳 $0.00001047 垃圾價。三個根因：
  C-1: 交易所 K線 Close 極小殘值（pump-dump 殘值）直傳給 LLM
  C-2: CoinGecko fallback 用文字模糊搜尋取 coins[0]，不驗 symbol → 回仿冒幣
  C-3: CMC 回 rank 不做 sanity check → 主流幣可能拿到仿冒幣資料

這些測試用 monkeypatch 模擬資料源回傳垃圾值，驗證 guard 攔截。
"""

import httpx
import pytest

from core.tools.coinmarketcap_tools import fetch_cmc_quote
from core.tools.crypto_modules import analysis as analysis_mod


@pytest.fixture(autouse=True)
def _clear_coingecko_cache():
    """每個測試前清空 CoinGecko in-memory cache，避免跨測試汙染。"""
    analysis_mod._coingecko_mem_cache.clear()
    yield
    analysis_mod._coingecko_mem_cache.clear()


# ============================================================================
# C-2: CoinGecko fallback 回錯幣（BTC 垃圾價的主因）
# ============================================================================


class _FakeResp:
    """模擬 httpx.Response。"""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "fake error", request=None, response=self
            )


def _make_fake_httpx_get(search_json, price_json):
    """造一個假的 httpx.get，依 URL 路由回 search/price JSON。

    CoinGecko fallback 在函式內 `import httpx as _httpx`（local binding），
    無法從外部 monkeypatch 模組屬性；但 _httpx.get 會解析到真實 httpx.get，
    所以 patch httpx.get 即可覆蓋兩條路徑（CoinGecko + CMC）。
    """

    def _fake_get(url, params=None, headers=None, **kwargs):
        if "search" in url:
            return _FakeResp(200, search_json)
        if "simple/price" in url:
            return _FakeResp(200, price_json)
        # CMC quotes/latest — 由各 CMC 測試用自己的 fake_get 覆蓋
        return _FakeResp(404, {})

    return _fake_get


def test_coingecko_rejects_impostor_coin(monkeypatch):
    """C-2 核心：CoinGecko 搜到結果但無任一 symbol 精確相符，必須拒回仿冒幣。

    場景：查 BTC，CoinGecko search 只回仿冒幣（symbol=BTCX/BTC2，均非 BTC）。
    修補前會取 coins[0]（rank 最佳）= 仿冒幣 → 回 $0.00001047 垃圾價。
    修補後因無 symbol 精確相符，明確拒回。
    """
    search_json = {
        "coins": [
            {"id": "bitcoin-impostor", "symbol": "BTCX", "name": "Bitcoin X",
             "market_cap_rank": 200},
            {"id": "btc2-coin", "symbol": "BTC2", "name": "BTC2",
             "market_cap_rank": 500},
        ]
    }
    price_json = {"bitcoin-impostor": {"usd": 0.00001047}}
    monkeypatch.setattr(httpx, "get", _make_fake_httpx_get(search_json, price_json))

    result = analysis_mod._coingecko_price_fallback("BTC")
    assert "counterfeit" in result or "match symbol" in result, (
        f"應拒回仿冒幣，但回傳: {result[:200]}"
    )
    assert "0.00001047" not in result, "垃圾價不該出現在回傳"


def test_coingecko_accepts_exact_symbol_match(monkeypatch):
    """C-2 正向：CoinGecko 回的幣 symbol 精確相符（BTC），應正常回價。"""
    search_json = {
        "coins": [
            {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin",
             "market_cap_rank": 1},
        ]
    }
    price_json = {"bitcoin": {"usd": 63712.0, "twd": 2056000, "usd_24h_change": -0.97,
                              "usd_market_cap": 1.278e12}}
    monkeypatch.setattr(httpx, "get", _make_fake_httpx_get(search_json, price_json))

    result = analysis_mod._coingecko_price_fallback("BTC")
    assert "Bitcoin" in result
    assert "63,712" in result or "63712" in result
    assert "BTCX" not in result  # 不該混入仿冒幣


def test_coingecko_rejects_zero_price(monkeypatch):
    """C-1/C-2：CoinGecko 回 usd=0（無流動性新幣），不該格式化成 $0.000000。"""
    search_json = {
        "coins": [{"id": "newcoin", "symbol": "NEW", "name": "New Coin",
                   "market_cap_rank": 999}]
    }
    price_json = {"newcoin": {"usd": 0}}
    monkeypatch.setattr(httpx, "get", _make_fake_httpx_get(search_json, price_json))

    result = analysis_mod._coingecko_price_fallback("NEW")
    assert "No valid price" in result or "invalid" in result, f"應拒回 usd=0: {result[:200]}"
    assert "$0.000000" not in result


def test_coingecko_rejects_none_price(monkeypatch):
    """C-1：CoinGecko 回 usd=None，不該 crash 也不該回垃圾。"""
    search_json = {
        "coins": [{"id": "x", "symbol": "XCOIN", "name": "X Coin",
                   "market_cap_rank": 500}]
    }
    price_json = {"x": {"usd": None}}
    monkeypatch.setattr(httpx, "get", _make_fake_httpx_get(search_json, price_json))

    result = analysis_mod._coingecko_price_fallback("XCOIN")
    assert "No valid price" in result or "invalid" in result


def test_coingecko_flags_majorcoin_low_price(monkeypatch):
    """C-1 加強：top-100 幣價格 < $1 極可能是仿冒幣，應標可疑拒回。"""
    search_json = {
        "coins": [{"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin",
                   "market_cap_rank": 1}]
    }
    # symbol 相符但價格異常低（真 BTC rank=1 但 price=0.5 → 矛盾 → 仿冒幣資料）
    price_json = {"bitcoin": {"usd": 0.5}}
    monkeypatch.setattr(httpx, "get", _make_fake_httpx_get(search_json, price_json))

    result = analysis_mod._coingecko_price_fallback("BTC")
    assert "suspicious" in result or "counterfeit" in result, (
        f"rank=1 但 price<$1 應標可疑: {result[:200]}"
    )


# ============================================================================
# C-3: CMC rank sanity
# ============================================================================


def test_cmc_flags_suspicious_rank(monkeypatch):
    """C-3：BTC 預期 rank=1，若回 rank=4500 應標 data_suspicious。"""

    def fake_get(url, params=None, headers=None, **kw):
        return _FakeResp(200, {
            "data": {
                "BTC": {
                    "name": "Some Impostor",
                    "cmc_rank": 4500,
                    "quote": {"USD": {
                        "price": 0.0001,
                        "market_cap": 10000,
                        "volume_24h": 50,
                    }},
                }
            }
        })

    monkeypatch.setattr(httpx, "get", fake_get)
    result = fetch_cmc_quote("BTC", "fake-key-for-test")
    assert result.get("data_suspicious") is True
    assert "4500" in result.get("suspicion_reason", "")


def test_cmc_normal_rank_not_flagged(monkeypatch):
    """C-3 正向：BTC rank=1 不該被標可疑。"""

    def fake_get(url, params=None, headers=None, **kw):
        return _FakeResp(200, {
            "data": {
                "BTC": {
                    "name": "Bitcoin",
                    "cmc_rank": 1,
                    "quote": {"USD": {
                        "price": 63000,
                        "market_cap": 1.2e12,
                        "volume_24h": 5e10,
                    }},
                }
            }
        })

    monkeypatch.setattr(httpx, "get", fake_get)
    result = fetch_cmc_quote("BTC", "fake-key-for-test")
    assert result.get("data_suspicious") is not True
    assert result["price"] == 63000
    assert result["cmc_rank"] == 1


def test_cmc_rejects_zero_price(monkeypatch):
    """C-3：price=0 不該當有效值回傳。"""

    def fake_get(url, params=None, headers=None, **kw):
        return _FakeResp(200, {
            "data": {
                "X": {
                    "name": "X", "cmc_rank": 999,
                    "quote": {"USD": {"price": 0}},
                }
            }
        })

    monkeypatch.setattr(httpx, "get", fake_get)
    result = fetch_cmc_quote("X", "fake-key-for-test")
    assert "error" in result
    assert "Invalid" in result["error"]


# ============================================================================
# C-1: 交易所 K線極小殘值（_fetch_from_exchange）
# ============================================================================


def test_fetch_from_exchange_rejects_zero_close(monkeypatch):
    """C-1：交易所 K線 Close=0 應回 None（走 fallback），不格式化成 $0。"""
    import pandas as pd


    # 造一個回 Close=0 的假 fetcher
    class _FakeFetcher:
        def get_historical_klines(self, sym, interval, limit):
            return pd.DataFrame({"Close": [0.0]})

    monkeypatch.setattr(
        "core.tools.crypto_modules.analysis.get_data_fetcher",
        lambda ex: _FakeFetcher(),
    )
    result = analysis_mod._fetch_from_exchange("BTC", "binance", "BTCUSDT")
    assert result is None, "Close=0 應回 None，不該回 $0 價格"


def test_fetch_from_exchange_rejects_negative_close(monkeypatch):
    """C-1：交易所 K線 Close<0（資料錯誤）也應回 None。"""
    import pandas as pd

    class _FakeFetcher:
        def get_historical_klines(self, sym, interval, limit):
            return pd.DataFrame({"Close": [-5.0]})

    monkeypatch.setattr(
        "core.tools.crypto_modules.analysis.get_data_fetcher",
        lambda ex: _FakeFetcher(),
    )
    result = analysis_mod._fetch_from_exchange("ETH", "okx", "ETH-USDT")
    assert result is None


def test_fetch_from_exchange_accepts_valid_close(monkeypatch):
    """C-1 正向：正常 Close 值應正常回傳價格字串。"""
    import pandas as pd

    class _FakeFetcher:
        def get_historical_klines(self, sym, interval, limit):
            return pd.DataFrame({"Close": [63766.9]})

    monkeypatch.setattr(
        "core.tools.crypto_modules.analysis.get_data_fetcher",
        lambda ex: _FakeFetcher(),
    )
    result = analysis_mod._fetch_from_exchange("BTC", "okx", "BTC-USDT")
    assert result is not None
    assert "Live Price" in result
    assert "63,766" in result or "63766" in result
