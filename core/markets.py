"""帳本／行事曆共用的市場判定：symbol → market（純函式）。

帳本表單只填代號不選市場（UX 第二輪），以前空市場的 trade 會被存成 ``cash``，
持倉就拿不到現價、也不會有財報事件（2026-09-12 抓到）。這裡用後綴與代號形狀判，
判不出來回 None（呼叫端決定退路）。
"""

from __future__ import annotations

import re
from typing import Optional

LEDGER_MARKETS: tuple[str, ...] = (
    "crypto",
    "tw_stock",
    "us_stock",
    "hk_stock",
    "jp_stock",
    "kr_stock",
    "cn_stock",
    "forex",
    "commodity",
)

_SUFFIX_TO_MARKET: dict[str, str] = {
    ".TW": "tw_stock",
    ".TWO": "tw_stock",
    ".HK": "hk_stock",
    ".T": "jp_stock",
    ".KS": "kr_stock",
    ".KQ": "kr_stock",
    ".SS": "cn_stock",
    ".SZ": "cn_stock",
    "=F": "commodity",
    "=X": "forex",
}
_FIAT = {
    "USD",
    "TWD",
    "JPY",
    "EUR",
    "GBP",
    "CNY",
    "HKD",
    "KRW",
    "AUD",
    "CAD",
    "CHF",
    "SGD",
    "NZD",
}
_COMMODITY_WORDS = {
    "GOLD",
    "SILVER",
    "OIL",
    "WTI",
    "BRENT",
    "XAU",
    "XAG",
    "COPPER",
    "NATGAS",
}


def infer_market(symbol: str) -> Optional[str]:
    """``2330.TW``→tw_stock、``0700.HK``→hk_stock、``7203.T``→jp_stock、``005930.KS``→kr_stock、
    ``600519.SS``→cn_stock、``BTC``→crypto、``AAPL``→us_stock、``2330``→tw_stock、
    ``600519``→cn_stock、``USDTWD``→forex、``GOLD``→commodity；其他 None。"""
    s = (symbol or "").strip().upper()
    if not s:
        return None
    for suffix, market in _SUFFIX_TO_MARKET.items():
        if s.endswith(suffix) and len(s) > len(suffix):
            return market
    if s.endswith("-USD") or s.endswith("USDT") and len(s) > 4:
        return "crypto"
    from core.tools.helpers import is_crypto_symbol

    if is_crypto_symbol(s):
        return "crypto"
    if s in _COMMODITY_WORDS:
        return "commodity"
    if re.fullmatch(r"\d{4}[A-Z]?", s) or re.fullmatch(r"00\d{3,4}[A-Z]?", s):
        return "tw_stock"  # 四位＝上市／上櫃；00 開頭五六位＝台股 ETF（00878、006208）
    if re.fullmatch(r"\d{6}", s):
        return "cn_stock"
    if re.fullmatch(r"[A-Z]{6}", s) and s[:3] in _FIAT and s[3:] in _FIAT:
        return "forex"
    if re.fullmatch(r"[A-Z]{3}/[A-Z]{3}", s):
        return "forex"
    if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", s):
        return "us_stock"
    return None


def strip_exchange_suffix(symbol: str) -> str:
    """``0700.HK``→``0700``；美股／加密的 ``.``（BRK.B）不動。"""
    s = (symbol or "").strip().upper()
    for suffix in _SUFFIX_TO_MARKET:
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s
