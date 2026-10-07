"""CcxtAdapter 契約對拍測試 — data/data_fetcher.py 的 CcxtAdapter。

驗證 adapter 的回傳結構與 BinanceDataFetcher/OkxDataFetcher 完全對齊：
- get_historical_klines → 12 欄 DataFrame（Open_time...Ignore），Open_time 為 Timestamp
- get_futures_data → tuple(DataFrame, funding_dict)，funding schema 依交易所
- symbol 格式轉換（對外 BTCUSDT/BTC-USDT ↔ CCXT BTC/USDT）
- get_data_fetcher 的 USE_CCXT 開關路由

用 mock CCXT instance（不打真實 API），聚焦於「映射正確性」。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.data_fetcher import (
    CcxtAdapter,
    SymbolNotFoundError,
    _from_ccxt_symbol,
    _to_ccxt_symbol,
    get_data_fetcher,
)

# ──────────────────────────────────────────────────────────────────────────────
# Symbol 轉換
# ──────────────────────────────────────────────────────────────────────────────


def test_to_ccxt_symbol_binance():
    assert _to_ccxt_symbol("BTCUSDT", "binance") == "BTC/USDT"
    assert _to_ccxt_symbol("ETHUSDT", "binance") == "ETH/USDT"


def test_to_ccxt_symbol_okx():
    assert _to_ccxt_symbol("BTC-USDT", "okx") == "BTC/USDT"


def test_to_ccxt_symbol_already_unified():
    assert _to_ccxt_symbol("BTC/USDT", "binance") == "BTC/USDT"


def test_from_ccxt_symbol_binance():
    assert _from_ccxt_symbol("BTC/USDT", "binance") == "BTCUSDT"


def test_from_ccxt_symbol_okx():
    assert _from_ccxt_symbol("BTC/USDT", "okx") == "BTC-USDT"


# ──────────────────────────────────────────────────────────────────────────────
# Adapter 12 欄 DataFrame 契約（mock CCXT）
# ──────────────────────────────────────────────────────────────────────────────

EXPECTED_COLUMNS = [
    "Open_time",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Close_time",
    "Quote_asset_volume",
    "Number_of_trades",
    "Taker_buy_base_asset_volume",
    "Taker_buy_quote_asset_volume",
    "Ignore",
]


def _make_adapter(exchange: str) -> CcxtAdapter:
    """建一個 adapter，但把內部 CCXT instance 換成 mock（不打真實 API）。"""
    with patch("ccxt.binance" if exchange == "binance" else "ccxt.okx") as mock_ctor:
        mock_ctor.return_value = MagicMock()
        return CcxtAdapter(exchange)


def test_get_historical_klines_returns_12_column_dataframe():
    adapter = _make_adapter("binance")
    # CCXT fetch_ohlcv 回 [ts, o, h, l, c, v]
    adapter._ccxt.fetch_ohlcv.return_value = [
        [1700000000000, 35000.0, 35100.0, 34900.0, 35050.0, 12.3],
        [1700003600000, 35050.0, 35200.0, 35000.0, 35150.0, 8.7],
    ]
    df = adapter.get_historical_klines("BTCUSDT", "1h", limit=2)

    assert df is not None
    assert list(df.columns) == EXPECTED_COLUMNS
    assert len(df) == 2
    # Open_time 應為 Timestamp
    assert pd.api.types.is_datetime64_any_dtype(df["Open_time"])
    # 數值欄位應為數值型
    for col in ("Open", "High", "Low", "Close", "Volume"):
        assert pd.api.types.is_numeric_dtype(df[col])
    # CCXT 不提供的欄位應補 0
    assert (df["Number_of_trades"] == 0).all()
    assert (df["Taker_buy_base_asset_volume"] == 0.0).all()


def test_get_historical_klines_empty_returns_none():
    adapter = _make_adapter("binance")
    adapter._ccxt.fetch_ohlcv.return_value = []
    assert adapter.get_historical_klines("BTCUSDT", "1h") is None


def test_get_historical_klines_api_error_returns_none():
    adapter = _make_adapter("binance")
    adapter._ccxt.fetch_ohlcv.side_effect = RuntimeError("network")
    assert adapter.get_historical_klines("BTCUSDT", "1h") is None


def test_get_historical_klines_passes_ccxt_symbol():
    """對外 BTCUSDT 應轉成 BTC/USDT 傳給 CCXT。"""
    adapter = _make_adapter("binance")
    adapter._ccxt.fetch_ohlcv.return_value = []
    adapter.get_historical_klines("BTCUSDT", "1h", limit=5)
    call_args = adapter._ccxt.fetch_ohlcv.call_args
    assert call_args[0][0] == "BTC/USDT"  # 第一位置參數 = symbol


# ──────────────────────────────────────────────────────────────────────────────
# Futures + funding schema
# ──────────────────────────────────────────────────────────────────────────────


def test_get_futures_data_binance_funding_schema():
    adapter = _make_adapter("binance")
    adapter._ccxt.fetch_ohlcv.return_value = [
        [1700000000000, 35000.0, 35100.0, 34900.0, 35050.0, 12.3]
    ]
    adapter._ccxt.fetch_funding_rate.return_value = {
        "fundingRate": 0.0001,
        "fundingDatetime": "2026-07-29T08:00:00+00:00",
    }
    df, funding = adapter.get_futures_data("BTCUSDT", "1h", limit=1)

    assert list(df.columns) == EXPECTED_COLUMNS
    assert "last_funding_rate" in funding
    assert "next_funding_time" in funding
    assert funding["last_funding_rate"] == pytest.approx(0.0001)


def test_get_futures_data_okx_funding_schema():
    adapter = _make_adapter("okx")
    adapter._ccxt.fetch_ohlcv.return_value = [
        [1700000000000, 35000.0, 35100.0, 34900.0, 35050.0, 12.3]
    ]
    adapter._ccxt.fetch_funding_rate.return_value = {
        "fundingRate": 0.0002,
        "nextFundingDatetime": "2026-07-29T16:00:00+00:00",
    }
    df, funding = adapter.get_futures_data("BTC-USDT", "1h", limit=1)

    assert list(df.columns) == EXPECTED_COLUMNS
    # OKX schema 應含這些 key（與 OkxDataFetcher 對齊）
    assert "current_funding_rate" in funding
    assert "next_funding_time" in funding


def test_get_futures_data_funding_error_returns_error_key():
    adapter = _make_adapter("binance")
    adapter._ccxt.fetch_ohlcv.return_value = [
        [1700000000000, 35000.0, 35100.0, 34900.0, 35050.0, 12.3]
    ]
    adapter._ccxt.fetch_funding_rate.side_effect = RuntimeError("api down")
    df, funding = adapter.get_futures_data("BTCUSDT", "1h", limit=1)

    assert df is not None
    assert funding.get("error") == "Failed to fetch funding rate"


# ──────────────────────────────────────────────────────────────────────────────
# symbol availability
# ──────────────────────────────────────────────────────────────────────────────


def test_check_symbol_availability_exists():
    adapter = _make_adapter("binance")
    adapter._ccxt.load_markets.return_value = {"BTC/USDT": {}}
    assert adapter.check_symbol_availability("BTCUSDT") is True


def test_check_symbol_availability_missing_raises():
    adapter = _make_adapter("binance")
    adapter._ccxt.load_markets.return_value = {"ETH/USDT": {}}
    with pytest.raises(SymbolNotFoundError):
        adapter.check_symbol_availability("BTCUSDT")


# ──────────────────────────────────────────────────────────────────────────────
# get_data_fetcher 的 USE_CCXT 路由
# ──────────────────────────────────────────────────────────────────────────────


def test_get_data_fetcher_default_uses_legacy(monkeypatch):
    """預設（USE_CCXT 未設）應回傳舊實作。"""
    import data.data_fetcher as mod

    monkeypatch.delenv("USE_CCXT", raising=False)
    # 清掉快取避免跨測試殘留
    mod._fetcher_instances.clear()
    fetcher = get_data_fetcher("binance")
    assert not isinstance(fetcher, CcxtAdapter)


def test_get_data_fetcher_with_ccxt_flag(monkeypatch):
    """USE_CCXT=1 應回傳 CcxtAdapter。"""
    import data.data_fetcher as mod

    monkeypatch.setenv("USE_CCXT", "1")
    mod._fetcher_instances.clear()
    with patch("ccxt.binance") as mock_ctor:
        mock_ctor.return_value = MagicMock()
        fetcher = get_data_fetcher("binance")
    assert isinstance(fetcher, CcxtAdapter)


def test_get_data_fetcher_ccxt_singleton_per_exchange(monkeypatch):
    """USE_CCXT=1 時，同一交易所應回傳同一個 adapter 實例（保留 rate-limit 狀態）。"""
    import data.data_fetcher as mod

    monkeypatch.setenv("USE_CCXT", "1")
    mod._fetcher_instances.clear()
    with patch("ccxt.binance") as mock_ctor:
        mock_ctor.return_value = MagicMock()
        a1 = get_data_fetcher("binance")
        a2 = get_data_fetcher("binance")
    assert a1 is a2
