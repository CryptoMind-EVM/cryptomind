"""熱門榜排除穩定幣（2026-09-27 上市準備 PR-2，design §3）。

OKX 全部 -USDT 交易對照 24h 成交量排序，USDC-USDT 常年第一——訪客打開行情頁第一眼
看到的「熱門」是一個永遠 1 美元的穩定幣。top_volume／漲幅／跌幅榜都要濾掉穩定幣；
使用者自己指定的清單（target_symbols）照他的選擇，不在這裡動（前端另外清）。
"""

from __future__ import annotations

import pytest

from analysis import crypto_screener_light as screener

pytestmark = pytest.mark.unit


def _okx(inst_id: str, last: float, open24h: float, vol: float) -> dict:
    return {
        "instId": inst_id,
        "last": str(last),
        "open24h": str(open24h),
        "volCcy24h": str(vol),
    }


OKX_TICKERS = [
    _okx("USDC-USDT", 1.0001, 0.9999, 9_000_000_000),
    _okx("BTC-USDT", 98_000, 97_000, 5_000_000_000),
    _okx("DAI-USDT", 0.9990, 1.0010, 4_000_000_000),
    _okx("ETH-USDT", 3_600, 3_500, 3_000_000_000),
    _okx("FDUSD-USDT", 1.0000, 1.0000, 2_500_000_000),
    _okx("SOL-USDT", 190, 180, 2_000_000_000),
    _okx("USDE-USDT", 0.9700, 1.0000, 1_500_000_000),  # 脫鉤 -3%：跌幅榜也不該出現
    _okx("PEPE-USDT", 0.00001, 0.000008, 900_000_000),
    _okx("DOGE-USDT", 0.2, 0.21, 800_000_000),
    _okx("BTC-USDC", 98_000, 97_000, 10_000_000_000),  # 非 -USDT，本來就不列
]

STABLE_OKX = {"USDC-USDT", "DAI-USDT", "FDUSD-USDT", "USDE-USDT"}


class _Fetcher:
    def __init__(self, tickers):
        self._tickers = tickers

    def get_tickers(self):
        return self._tickers


@pytest.fixture
def okx(monkeypatch):
    monkeypatch.setattr(
        screener,
        "_TICKER_CACHE",
        {"okx": {"data": [], "timestamp": 0}, "binance": {"data": [], "timestamp": 0}},
    )
    monkeypatch.setattr(screener, "get_data_fetcher", lambda _ex: _Fetcher(OKX_TICKERS))


def _symbols(df) -> list[str]:
    return list(df["Symbol"]) if not df.empty else []


def test_top_volume_skips_stablecoins(okx):
    vol, _g, _l, _ = screener.screen_top_cryptos_light(exchange="okx", limit=3)
    assert _symbols(vol) == ["BTC-USDT", "ETH-USDT", "SOL-USDT"]


def test_gainers_and_losers_skip_stablecoins(okx):
    _v, gainers, losers, _ = screener.screen_top_cryptos_light(exchange="okx", limit=5)
    assert not STABLE_OKX & set(_symbols(gainers))
    assert not STABLE_OKX & set(_symbols(losers))
    assert _symbols(gainers)[0] == "PEPE-USDT"
    assert _symbols(losers)[0] == "DOGE-USDT"


def test_explicit_target_symbols_are_left_alone(okx):
    vol, _g, _l, _ = screener.screen_top_cryptos_light(
        exchange="okx", limit=2, target_symbols=["USDC-USDT", "BTC-USDT"]
    )
    assert set(_symbols(vol)) == {"USDC-USDT", "BTC-USDT"}


def test_binance_symbols_are_recognised(monkeypatch):
    monkeypatch.setattr(
        screener,
        "_TICKER_CACHE",
        {"okx": {"data": [], "timestamp": 0}, "binance": {"data": [], "timestamp": 0}},
    )
    tickers = [
        {
            "symbol": "FDUSDUSDT",
            "lastPrice": "1",
            "priceChangePercent": "0",
            "quoteVolume": "9e9",
        },
        {
            "symbol": "USDCUSDT",
            "lastPrice": "1",
            "priceChangePercent": "0",
            "quoteVolume": "8e9",
        },
        {
            "symbol": "BTCUSDT",
            "lastPrice": "98000",
            "priceChangePercent": "1",
            "quoteVolume": "5e9",
        },
    ]
    monkeypatch.setattr(screener, "get_data_fetcher", lambda _ex: _Fetcher(tickers))
    vol, _g, _l, _ = screener.screen_top_cryptos_light(exchange="binance", limit=3)
    assert _symbols(vol) == ["BTCUSDT"]


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("USDC-USDT", True),
        ("usdt-usdc", True),
        ("PYUSD-USDT", True),
        ("EURC-USDT", True),
        ("USDCUSDT", True),
        ("DAI/USDT", True),
        ("BTC-USDT", False),
        ("PAXG-USDT", False),
        ("USUAL-USDT", False),
        ("", False),
    ],
)
def test_is_stablecoin_symbol(symbol, expected):
    assert screener.is_stablecoin_symbol(symbol) is expected


def test_frontend_list_matches_backend():
    """前端 filter.js 有同一份清單（自動釘選／已存清單用）——兩邊不能漂。"""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "web/js/filter.js").read_text(
        encoding="utf-8"
    )
    block = re.search(r"const STABLECOIN_BASES = new Set\(\[(.*?)\]\);", src, re.S)
    assert block
    assert set(re.findall(r"'([A-Z0-9]+)'", block.group(1))) == set(
        screener.STABLECOIN_BASES
    )
