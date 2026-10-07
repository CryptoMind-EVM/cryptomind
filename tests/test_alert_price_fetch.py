"""api/alert_checker._fetch_price 的台股／美股路徑（2026-09-27）。

*_stock_price 是 LangChain StructuredTool：直接呼叫會 TypeError、被吞成 None——
正式機實測 2330／AAPL 都抓不到，台股／美股價格警報從工具化後就沒觸發過。
這裡用真的 StructuredTool（只換掉底層函式）確認一定走 .invoke()。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.tools import StructuredTool

pytestmark = pytest.mark.unit


def _tool(name, fn):
    return StructuredTool.from_function(func=fn, name=name, description=name)


async def test_tw_stock_uses_invoke_and_prev_close():
    from api.alert_checker import _fetch_price

    def tw(ticker: str) -> dict:
        assert ticker == "2330"
        return {"ticker": "2330.TW", "current_price": 2475.0, "prev_close": 2495.0}

    with patch("core.tools.tw_stock_tools.tw_stock_price", _tool("tw_stock_price", tw)):
        assert await _fetch_price("2330", "tw_stock") == (2475.0, 2495.0)


async def test_tw_stock_unknown_symbol_is_none():
    from api.alert_checker import _fetch_price

    def tw(ticker: str) -> dict:
        return {"ticker": f"{ticker}.TW", "current_price": None, "prev_close": None}

    with patch("core.tools.tw_stock_tools.tw_stock_price", _tool("tw_stock_price", tw)):
        assert await _fetch_price("9999", "tw_stock") is None


async def test_us_stock_uses_invoke_and_prev_close():
    from api.alert_checker import _fetch_price

    def us(symbol: str) -> dict:
        assert symbol == "AAPL"
        return {"symbol": "AAPL", "price": 341.07, "prev_close": 335.91}

    with patch("core.tools.us_stock_tools.us_stock_price", _tool("us_stock_price", us)):
        assert await _fetch_price("AAPL", "us_stock") == (341.07, 335.91)


async def test_us_stock_error_is_none():
    from api.alert_checker import _fetch_price

    def us(symbol: str) -> dict:
        return {"symbol": symbol, "error": "'exchangeTimezoneName'"}

    with patch("core.tools.us_stock_tools.us_stock_price", _tool("us_stock_price", us)):
        assert await _fetch_price("ZZZZZQ", "us_stock") is None


async def test_change_pct_alert_uses_daily_change():
    """漲跌％警報：現價對前一日收盤。"""
    from api.alert_checker import is_condition_met

    assert is_condition_met("change_pct_down", 0.5, 2475.0, 2495.0) is True
    assert is_condition_met("change_pct_up", 0.5, 2475.0, 2495.0) is False


@pytest.mark.parametrize(
    "market,code",
    [("hk_stock", "hk"), ("jp_stock", "jp"), ("kr_stock", "kr"), ("cn_stock", "cn"), ("in_stock", "in")],
)
async def test_asia_markets_use_provider(market, code):
    """港／日／韓／陸／印股：跟各市場分頁同一個 provider，基準是前一日收盤。"""
    from api.alert_checker import _fetch_price

    class _Provider:
        def get_price(self, symbol):
            assert symbol == "0700.HK"
            return {"symbol": symbol, "price": 520.0, "prev_close": 500.0}

    with patch("core.providers.base_provider.get_provider", return_value=_Provider()) as gp:
        assert await _fetch_price("0700.HK", market) == (520.0, 500.0)
    gp.assert_called_once_with(code)


async def test_provider_error_is_none():
    from api.alert_checker import _fetch_price

    class _Provider:
        def get_price(self, symbol):
            return {"symbol": symbol, "error": "無法取得報價"}

    with patch("core.providers.base_provider.get_provider", return_value=_Provider()):
        assert await _fetch_price("9999.HK", "hk_stock") is None


@pytest.mark.parametrize("market,symbol", [("commodity", "GC=F"), ("forex", "EURUSD=X")])
async def test_commodity_and_forex_use_yfinance(market, symbol):
    from api.alert_checker import _fetch_price

    with patch("api.alert_checker._yf_quote", return_value=(2400.5, 2390.0)) as q:
        assert await _fetch_price(symbol, market) == (2400.5, 2390.0)
    q.assert_called_once_with(symbol)


def test_yf_quote_rejects_nan_and_missing():
    from api import alert_checker

    class _FI:
        def __init__(self, last, prev):
            self.last_price, self.previous_close = last, prev

    class _Ticker:
        def __init__(self, fi):
            self.fast_info = fi

    import yfinance

    with patch.object(yfinance, "Ticker", return_value=_Ticker(_FI(float("nan"), 1.0))):
        assert alert_checker._yf_quote("ZZ=F") is None
    with patch.object(yfinance, "Ticker", return_value=_Ticker(_FI(1.08, float("nan")))):
        assert alert_checker._yf_quote("EURUSD=X") == (1.08, 1.08)
