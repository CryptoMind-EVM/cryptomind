"""歷史收盤價（某一天）——股票／ETF 走 yfinance，幣走 OKX 日 K（退 Binance）。

「某一天的收盤」對股票是「那天或之前最近一個交易日」的收盤；幣是 UTC 日 K 的收盤。
同一 (symbol, date) 一天內只抓一次（共用快取）。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

import httpx

from core.providers.base_provider import normalize_symbol

logger = logging.getLogger(__name__)

_MARKET_CODE = {
    "tw_stock": "tw",
    "us_stock": "us",
    "hk_stock": "hk",
    "jp_stock": "jp",
    "kr_stock": "kr",
    "cn_stock": "cn",
}
_STABLE = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "PYUSD", "USDS"}
# TON→GRAM：Toncoin 2026-06-15 改名，交易所只剩 GRAM 交易對（帳本符號仍記 TON）
_ALIAS = {"WETH": "ETH", "WBTC": "BTC", "CBBTC": "BTC", "TON": "GRAM"}
CACHE_TTL = 24 * 3600
_local: Dict[str, float] = {}


def _cache_key(symbol: str, market: str, on: date) -> str:
    return f"scorecard:close:v1:{market}:{symbol.upper()}:{on.isoformat()}"


def _cached(key: str) -> Optional[float]:
    if key in _local:
        return _local[key]
    try:
        from core.database.cache import get_cache

        v = get_cache(key)
        if isinstance(v, (int, float)) and v > 0:
            _local[key] = float(v)
            return float(v)
    except Exception:  # noqa: BLE001
        pass
    return None


def _store(key: str, value: float) -> None:
    _local[key] = value
    try:
        from core.database.cache import set_cache

        set_cache(key, value, ttl=CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass


def stock_close_on(symbol: str, market: str, on: date) -> Optional[float]:
    """yfinance：抓 on−10 天到 on＋1 天，取 ≤ on 的最後一個收盤。"""
    import yfinance as yf

    code = _MARKET_CODE.get(market)
    yf_symbol = normalize_symbol(symbol, code) if code else symbol
    hist = yf.Ticker(yf_symbol).history(
        start=(on - timedelta(days=10)).isoformat(),
        end=(on + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
    )
    if hist is None or hist.empty:
        return None
    rows = [
        (idx.date(), float(row["Close"]))
        for idx, row in hist.iterrows()
        if row.get("Close") == row.get("Close")
    ]
    rows = [r for r in rows if r[0] <= on]
    return rows[-1][1] if rows else None


def crypto_close_on(symbol: str, on: date) -> Optional[float]:
    """OKX ``history-candles`` 1Dutc（after＝隔天 0 點 ms 取前一根）；退 Binance klines。"""
    sym = _ALIAS.get(symbol.upper(), symbol.upper())
    if sym in _STABLE:
        return 1.0
    day_start = datetime(on.year, on.month, on.day, tzinfo=timezone.utc)
    next_ms = int((day_start + timedelta(days=1)).timestamp() * 1000)
    try:
        resp = httpx.get(
            "https://www.okx.com/api/v5/market/history-candles",
            params={
                "instId": f"{sym}-USDT",
                "bar": "1Dutc",
                "after": str(next_ms),
                "limit": "1",
            },
            timeout=10.0,
        )
        rows = resp.json().get("data") or [] if resp.status_code == 200 else []
        if rows and int(rows[0][0]) == int(day_start.timestamp() * 1000):
            return float(rows[0][4])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[scorecard] okx %s %s: %s", sym, on, type(exc).__name__)
    try:
        resp = httpx.get(
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol": f"{sym}USDT",
                "interval": "1d",
                "startTime": int(day_start.timestamp() * 1000),
                "limit": 1,
            },
            timeout=10.0,
        )
        rows = resp.json() if resp.status_code == 200 else []
        if rows and int(rows[0][0]) == int(day_start.timestamp() * 1000):
            return float(rows[0][4])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[scorecard] binance %s %s: %s", sym, on, type(exc).__name__)
    # 第三順位：CoinGecko history（幣還沒上交易所的日子；只查已知 id，免費層限流所以放最後）
    try:
        from core.tools.crypto_modules.exchange_rate import _COINGECKO_IDS

        gecko_id = _COINGECKO_IDS.get(sym)
        if gecko_id:
            resp = httpx.get(
                f"https://api.coingecko.com/api/v3/coins/{gecko_id}/history",
                params={"date": on.strftime("%d-%m-%Y"), "localization": "false"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                price = (
                    (resp.json().get("market_data") or {}).get("current_price") or {}
                ).get("usd")
                if price:
                    return float(price)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[scorecard] coingecko %s %s: %s", sym, on, type(exc).__name__)
    return None


def close_on(symbol: str, market: str, on: date) -> Optional[float]:
    """統一入口（含快取）。基準 ETF 帶後綴時 market 給對應市場即可。"""
    key = _cache_key(symbol, market, on)
    hit = _cached(key)
    if hit is not None:
        return hit
    try:
        value = (
            crypto_close_on(symbol, on)
            if market == "crypto"
            else stock_close_on(symbol, market, on)
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "[scorecard] close_on %s %s %s failed: %s",
            market,
            symbol,
            on,
            type(exc).__name__,
        )
        value = None
    if value is not None and value > 0:
        _store(key, float(value))
        return float(value)
    return None
