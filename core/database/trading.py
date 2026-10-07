"""
交易相關資料庫操作
包含：自選清單

Refactored to use DatabaseBase for unified CRUD operations.
"""

import re
from typing import Dict, List, Optional

from .base import DatabaseBase

# ============================================================================
# 自選清單（user_watchlist，c056）：一列＝使用者＋市場＋代號
# 早報的「👀 自選清單」、行事曆同步、設定頁的價格警報入口都讀這裡。
# ============================================================================

# 抓得到報價的市場（api/alert_checker._fetch_price）；早報列價格、價格警報都靠它。
# 2026-09-27 第二階段：跟各市場分頁一致，10 個市場都能加（代號格式＝各分頁用的 yfinance 寫法）。
WATCHLIST_MARKETS = (
    "crypto",
    "tw_stock",
    "us_stock",
    "hk_stock",
    "jp_stock",
    "kr_stock",
    "cn_stock",
    "in_stock",
    "commodity",
    "forex",
)
MAX_WATCHLIST = 50

_TW_RE = re.compile(r"\d{4,6}[A-Z]?")
_US_RE = re.compile(r"[A-Z][A-Z0-9]{0,6}([.-][A-Z]{1,2})?")
_CRYPTO_RE = re.compile(r"[A-Z0-9]{2,15}")
_JP_RE = re.compile(r"\d{3}[0-9A-Z]")
_IN_RE = re.compile(r"[A-Z0-9][A-Z0-9&-]{0,19}")


def _split_suffix(s: str, suffixes: tuple) -> tuple:
    for suffix in suffixes:
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)], suffix
    return s, ""


def normalize_symbol(market: str, raw: str) -> Optional[str]:
    """使用者輸入 → 存進 DB 的代號；格式不合回 None（還沒查報價，只看長相）。

    crypto：BTC、btc-usdt、BTC/USDT、BTCUSDT → BTC
    tw_stock：2330、2330.TW、00878 → 2330／00878
    us_stock：aapl、BRK.B、BRK-B → AAPL／BRK.B／BRK-B
    hk_stock：700、0700、0700.HK → 0700.HK
    jp_stock：7203、7203.T → 7203.T
    kr_stock：005930、005930.KS、035720.KQ → 005930.KS／035720.KQ
    cn_stock：600519 → 600519.SS、000858 → 000858.SZ
    in_stock：reliance、RELIANCE.NS、TCS.BO → RELIANCE.NS／TCS.BO
    commodity：GC、gc=f → GC=F
    forex：EUR/USD、EURUSD、eurusd=x → EURUSD=X；TWD → TWD=X（以美元計）
    """
    s = (raw or "").strip().upper().replace(" ", "")
    if market == "crypto":
        s = s.replace("/", "").replace("-", "")
        for quote in ("USDT", "USDC", "USD"):
            if s.endswith(quote) and len(s) > len(quote):
                s = s[: -len(quote)]
                break
        return s if _CRYPTO_RE.fullmatch(s) else None
    if market == "tw_stock":
        s, _ = _split_suffix(s, (".TWO", ".TW"))
        return s if _TW_RE.fullmatch(s) else None
    if market == "us_stock":
        return s if _US_RE.fullmatch(s) else None
    if market == "hk_stock":
        s, _ = _split_suffix(s, (".HK",))
        if not s.isdecimal() or len(s) > 5:
            return None
        return f"{int(s):04d}.HK"
    if market == "jp_stock":
        s, _ = _split_suffix(s, (".T",))
        return f"{s}.T" if _JP_RE.fullmatch(s) else None
    if market == "kr_stock":
        s, suffix = _split_suffix(s, (".KS", ".KQ"))
        return f"{s}{suffix or '.KS'}" if s.isdecimal() and len(s) == 6 else None
    if market == "cn_stock":
        s, suffix = _split_suffix(s, (".SS", ".SZ"))
        if not (s.isdecimal() and len(s) == 6):
            return None
        return f"{s}{suffix or ('.SS' if s[0] in '69' else '.SZ')}"
    if market == "in_stock":
        s, suffix = _split_suffix(s, (".NS", ".BO"))
        return f"{s}{suffix or '.NS'}" if _IN_RE.fullmatch(s) else None
    if market == "commodity":
        s, _ = _split_suffix(s, ("=F",))
        return f"{s}=F" if re.fullmatch(r"[A-Z]{1,4}", s) else None
    if market == "forex":
        s, _ = _split_suffix(s.replace("/", ""), ("=X",))
        return f"{s}=X" if re.fullmatch(r"[A-Z]{3}|[A-Z]{6}", s) else None
    return None


def get_watchlist(user_id: str) -> List[Dict[str, str]]:
    """[{market, symbol}]，照加入的先後排。"""
    rows = DatabaseBase.query_all(
        "SELECT market, symbol FROM user_watchlist WHERE user_id = %s "
        "ORDER BY created_at, market, symbol",
        (user_id,),
    )
    return [{"market": r["market"], "symbol": r["symbol"]} for r in rows]


def add_to_watchlist(user_id: str, market: str, symbol: str) -> bool:
    """回傳是否真的新增（已存在回 False）。"""
    return (
        DatabaseBase.execute(
            "INSERT INTO user_watchlist (user_id, market, symbol) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (user_id, market, symbol),
        )
        > 0
    )


def replace_market(user_id: str, market: str, symbols: List[str]) -> List[str]:
    """把這個市場的自選整批設成 ``symbols``（呼叫端已正規化、去重）；其他市場不動。

    各市場分頁存的是「這個市場我要看哪些」的整份清單（web/js/watchlist-sync.js）。
    新加的照給的順序排（created_at 每檔差 1 毫秒），已存在的保留原本的加入時間。
    """
    from .base import transaction

    with transaction() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT symbol FROM user_watchlist WHERE user_id = %s AND market = %s",
            (user_id, market),
        )
        existing = {row[0] for row in c.fetchall()}
        keep = set(symbols)
        for sym in existing - keep:
            c.execute(
                "DELETE FROM user_watchlist WHERE user_id = %s AND market = %s AND symbol = %s",
                (user_id, market, sym),
            )
        for i, sym in enumerate(symbols):
            if sym in existing:
                continue
            c.execute(
                "INSERT INTO user_watchlist (user_id, market, symbol, created_at) "
                "VALUES (%s, %s, %s, NOW() + (%s * INTERVAL '1 millisecond')) "
                "ON CONFLICT DO NOTHING",
                (user_id, market, sym, i),
            )
    return symbols


def remove_from_watchlist(user_id: str, market: str, symbol: str) -> bool:
    return (
        DatabaseBase.execute(
            "DELETE FROM user_watchlist WHERE user_id = %s AND market = %s AND symbol = %s",
            (user_id, market, symbol),
        )
        > 0
    )
