"""symbol → market 共用判定（2026-09-12）：帳本空市場 trade 不再存成 cash；行事曆自選清單推市場。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.markets import LEDGER_MARKETS, infer_market, strip_exchange_suffix

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("2330.TW", "tw_stock"),
        ("2330", "tw_stock"),
        ("00878", "tw_stock"),
        ("006208", "tw_stock"),
        ("0700.HK", "hk_stock"),
        ("7203.T", "jp_stock"),
        ("005930.KS", "kr_stock"),
        ("035420.KQ", "kr_stock"),
        ("600519.SS", "cn_stock"),
        ("000858.SZ", "cn_stock"),
        ("600519", "cn_stock"),
        ("BTC", "crypto"),
        ("eth-usd", "crypto"),
        ("AAPL", "us_stock"),
        ("BRK.B", "us_stock"),
        ("USDTWD", "forex"),
        ("EUR/USD", "forex"),
        ("TWD=X", "forex"),
        ("GOLD", "commodity"),
        ("GC=F", "commodity"),
        ("", None),
        ("xyzabc!", None),
    ],
)
def test_infer_market(symbol, expected):
    assert infer_market(symbol) == expected


def test_strip_suffix_keeps_us_dot_tickers():
    assert strip_exchange_suffix("0700.HK") == "0700"
    assert strip_exchange_suffix("2330.tw") == "2330"
    assert strip_exchange_suffix("BRK.B") == "BRK.B"
    assert strip_exchange_suffix("GC=F") == "GC"


def test_ledger_markets_match_repo_and_units():
    from core.orm.trade_journal_repo import VALID_MARKETS
    from core.tools.crypto_modules.trade_journal import _STOCK_PROVIDER_MARKET

    assert set(LEDGER_MARKETS) == VALID_MARKETS
    for market in ("kr_stock", "cn_stock"):
        assert market in VALID_MARKETS and market in _STOCK_PROVIDER_MARKET
    repo = (REPO / "core" / "orm" / "trade_journal_repo.py").read_text(encoding="utf-8")
    assert 'if entry_type == "trade" and not (market or "").strip():' in repo
    assert "market = infer_market(symbol) or market" in repo
    js = (REPO / "web" / "js" / "components" / "tab-journal.js").read_text(
        encoding="utf-8"
    )
    assert "kr_stock:" in js and "cn_stock:" in js
