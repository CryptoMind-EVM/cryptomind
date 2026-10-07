"""
Commodity Market Router — 大宗商品市場
Data source: yfinance (futures & ETF symbols)
Follows the same pattern as api/routers/usstock.py
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api.deps import get_optional_current_user
from api.middleware.rate_limit import limiter
from api.routers._ttl_cache import TTLCache
from api.routers.yf_helpers import fetch_news, quote_extras
from api.user_llm import resolve_user_llm_credentials
from api.utils import logger
from utils.lazy_import import lazy_module

yf = lazy_module("yfinance")  # 第一次用到才載入（見 utils/lazy_import.py）

router = APIRouter(prefix="/api/commodity", tags=["Commodity"])

MARKET_DATA_UNAVAILABLE_MESSAGE = "目前無法取得商品行情，已回傳空資料供前端安全降級"

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache = TTLCache(default_ttl=600)
_get_cache = _cache.get
_set_cache = _cache.set


# ── Known symbols metadata (covers all 15 frontend picker options) ──────────────
DEFAULT_COMMODITIES = [
    {"symbol": "GC=F", "name": "黃金 Gold", "unit": "USD/oz"},
    {"symbol": "SI=F", "name": "白銀 Silver", "unit": "USD/oz"},
    {"symbol": "PL=F", "name": "鉑金 Platinum", "unit": "USD/oz"},
    {"symbol": "PA=F", "name": "鈀金 Palladium", "unit": "USD/oz"},
    {"symbol": "CL=F", "name": "WTI 原油", "unit": "USD/bbl"},
    {"symbol": "BZ=F", "name": "布蘭特原油 Brent", "unit": "USD/bbl"},
    {"symbol": "NG=F", "name": "天然氣 Nat.Gas", "unit": "USD/MMBtu"},
    {"symbol": "HG=F", "name": "銅 Copper", "unit": "USD/lb"},
    {"symbol": "ALI=F", "name": "鋁 Aluminum", "unit": "USD/lb"},
    {"symbol": "ZW=F", "name": "小麥 Wheat", "unit": "USD/bu"},
    {"symbol": "ZC=F", "name": "玉米 Corn", "unit": "USD/bu"},
    {"symbol": "ZS=F", "name": "黃豆 Soybeans", "unit": "USD/bu"},
    {"symbol": "KC=F", "name": "咖啡 Coffee", "unit": "USD/lb"},
    {"symbol": "SB=F", "name": "糖 Sugar", "unit": "USD/lb"},
    {"symbol": "CT=F", "name": "棉花 Cotton", "unit": "USD/lb"},
]


# ── Helpers ─────────────────────────────────────────────────────────────────────
def _fetch_commodity_sync(symbol: str, name: str, unit: str) -> dict | None:
    """Fetch a single commodity quote synchronously (run in thread)."""
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info
        price = round(float(fi.last_price), 4)
        prev = round(float(fi.previous_close), 4)
        chg = round(price - prev, 4)
        chg_p = round((chg / prev) * 100, 2) if prev else 0.0
        return {
            "symbol": symbol,
            "name": name,
            "price": price,
            "change": chg,
            "changePercent": chg_p,
            "unit": unit,
            **quote_extras(fi, 4),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[commodity] quote failed {symbol}: {e}")
        return None


def _fetch_technicals_sync(symbol: str) -> dict:
    """Compute RSI(14) and MACD from daily history."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo", interval="1d")
        if hist.empty or len(hist) < 15:
            return {}
        closes = hist["Close"]

        # RSI(14)
        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = round(float(100 - (100 / (1 + rs.iloc[-1]))), 1)

        # MACD
        ema12 = closes.ewm(span=12).mean()
        ema26 = closes.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9).mean()
        histogram = round(float(macd_line.iloc[-1] - signal.iloc[-1]), 4)

        # MA20 / MA50
        ma20 = (
            round(float(closes.rolling(20).mean().iloc[-1]), 4)
            if len(closes) >= 20
            else None
        )
        ma50 = (
            round(float(closes.rolling(50).mean().iloc[-1]), 4)
            if len(closes) >= 50
            else None
        )

        # 52-week high/low
        hist_52w = ticker.history(period="1y", interval="1d")
        high_52w = (
            round(float(hist_52w["High"].max()), 4) if not hist_52w.empty else None
        )
        low_52w = round(float(hist_52w["Low"].min()), 4) if not hist_52w.empty else None

        return {
            "rsi": rsi,
            "macd_histogram": histogram,
            "ma20": ma20,
            "ma50": ma50,
            "52w_high": high_52w,
            "52w_low": low_52w,
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[commodity] technicals failed {symbol}: {e}")
        return {}


# ── Endpoints ───────────────────────────────────────────────────────────────────


@router.get("/market")
async def get_commodity_market(symbols: Optional[str] = None):
    """Current price data for default commodities or custom symbols."""
    if symbols:
        targets = []
        for s in symbols.split(","):
            s = s.strip().upper()
            # Find name from defaults, or use symbol as name
            meta = next((c for c in DEFAULT_COMMODITIES if c["symbol"] == s), None)
            targets.append(meta or {"symbol": s, "name": s, "unit": "USD"})
    else:
        targets = DEFAULT_COMMODITIES

    cache_key = "market:" + ",".join(t["symbol"] for t in targets)
    cached = _get_cache(cache_key)
    if cached:
        return cached

    results = await asyncio.gather(
        *[
            asyncio.to_thread(_fetch_commodity_sync, t["symbol"], t["name"], t["unit"])
            for t in targets
        ]
    )
    commodities = [r for r in results if r]
    data = {
        "quotes": commodities,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    if len(commodities) != len(targets):
        data["partial_failure"] = True
        data["warning"] = MARKET_DATA_UNAVAILABLE_MESSAGE

    _set_cache(cache_key, data)
    return data


@limiter.limit("20/minute")



@router.get("/pulse/{symbol}")
async def get_commodity_pulse(
    request: Request,
    symbol: str,
    deep_analysis: bool = False,
    force_refresh: bool = False,
    lang: str = "zh-TW",
    x_user_llm_provider: Optional[str] = Header(None),
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """Market pulse analysis for a single commodity."""
    sym = symbol.upper()
    cache_key = f"pulse:{sym}"
    if not deep_analysis:
        cached = _get_cache(cache_key)
        if cached:
            return cached

    # Find display name
    meta = next((c for c in DEFAULT_COMMODITIES if c["symbol"] == sym), None)
    display_name = meta["name"] if meta else sym
    unit = meta["unit"] if meta else "USD"

    quote, tech, news_items = await asyncio.gather(
        asyncio.to_thread(_fetch_commodity_sync, sym, display_name, unit),
        asyncio.to_thread(_fetch_technicals_sync, sym),
        fetch_news([sym], limit_per=5, market="commodity", names=None, lang=lang),
    )

    if not quote:
        raise HTTPException(
            status_code=404, detail=f"Commodity symbol \"{sym}\" not found or data is currently unavailable"
        )

    price = quote["price"]
    chg_p = quote["changePercent"]
    trend_str = "走勢偏強" if chg_p > 0 else ("走勢偏弱" if chg_p < 0 else "走勢持平")
    rsi = tech.get("rsi")
    rsi_str = ""
    if isinstance(rsi, (int, float)):
        if rsi > 70:
            rsi_str = "，RSI 顯示可能處於超買區間"
        elif rsi < 30:
            rsi_str = "，RSI 顯示可能處於超賣區間"
        else:
            rsi_str = "，RSI 落在中性區間"

    summary = (
        f"{display_name} ({sym}) 目前報價為 {price} {unit}，"
        f"24小時{trend_str} ({chg_p:+.2f}%){rsi_str}。"
    )

    key_points = [
        f"RSI(14): {tech.get('rsi', 'N/A')}",
        f"MACD Histogram: {tech.get('macd_histogram', 'N/A')}",
        f"52W High: {tech.get('52w_high', 'N/A')} {unit}",
        f"52W Low: {tech.get('52w_low', 'N/A')} {unit}",
        f"24H Change: {chg_p:+.2f}%",
    ]

    source_mode = "on_demand"
    credentials = None
    ai_error = None
    cached_at = None
    cache_expires_at = None
    cooldown_remaining = None
    if deep_analysis:
        credentials = await resolve_user_llm_credentials(
            current_user, x_user_llm_provider
        )
    if credentials:
        from api.routers.deep_analysis_helper import get_deep_analysis

        news_str = ""
        if news_items:
            headlines = "\n".join(f"- {n['title']}" for n in news_items[:5])
            news_str = f"\n近期新聞（最新5條）:\n{headlines}"

        context = (
            f"商品: {display_name} ({sym})\n"
            f"現價: {price} {unit}\n"
            f"24H 漲跌幅: {chg_p:+.2f}%\n"
            f"RSI(14): {tech.get('rsi', 'N/A')}\n"
            f"MACD Histogram: {tech.get('macd_histogram', 'N/A')}\n"
            f"MA20: {tech.get('ma20', 'N/A')} {unit}\n"
            f"MA50: {tech.get('ma50', 'N/A')} {unit}\n"
            f"52W High: {tech.get('52w_high', 'N/A')} {unit}\n"
            f"52W Low: {tech.get('52w_low', 'N/A')} {unit}"
            f"{news_str}"
        )
        ai_result = await get_deep_analysis(
            market="commodity",
            symbol=sym,
            context=context,
            llm_key=credentials["api_key"],
            llm_provider=credentials["provider"],
            llm_model=credentials.get("model"),
            force_refresh=force_refresh,
            user_id=(current_user or {}).get("user_id", "anon"),
            language=lang,
        )
        source_mode = ai_result.get("source_mode", source_mode)
        ai_error = ai_result.get("ai_error")
        cached_at = ai_result.get("cached_at")
        cache_expires_at = ai_result.get("cache_expires_at")
        cooldown_remaining = ai_result.get("cooldown_remaining")
        if source_mode == "deep_analysis" and ai_result.get("report"):
            summary = ai_result["report"].get("summary", summary)

    result = {
        "symbol": sym,
        "name": display_name,
        "current_price": price,
        "unit": unit,
        "currency": unit,
        "change_24h": chg_p,
        "source_mode": source_mode,
        "ai_error": ai_error,
        "report": {
            "summary": summary,
            "key_points": key_points if source_mode != "deep_analysis" else [],
        },
        "technical_indicators": tech,
        "news": [
            {
                "title": n["title"],
                "url": n.get("url", ""),
                "published": n.get("published", ""),
            }
            for n in (news_items or [])[:5]
        ],
        "cached_at": cached_at,
        "cache_expires_at": cache_expires_at,
        "cooldown_remaining": cooldown_remaining,
    }
    if not deep_analysis:
        _set_cache(cache_key, result, ttl=300)
    return result


@limiter.limit("30/minute")



@router.get("/klines/{symbol}")
async def get_commodity_klines(request: Request, symbol: str, interval: str = "1d", limit: int = 200):
    """Historical OHLCV kline data for charting."""
    sym = symbol.upper()
    cache_key = f"klines:{sym}:{interval}"
    cached = _get_cache(cache_key)
    if cached:
        return cached

    period_map = {"1d": "1y", "1wk": "2y", "1mo": "5y"}
    period = period_map.get(interval, "1y")

    def fetch():
        ticker = yf.Ticker(sym)
        hist = ticker.history(period=period, interval=interval)
        if hist.empty:
            raise ValueError("無交易資料")
        klines = []
        for idx, row in hist.iterrows():
            try:
                klines.append(
                    {
                        "time": idx.strftime("%Y-%m-%d"),
                        "open": round(float(row["Open"]), 4),
                        "high": round(float(row["High"]), 4),
                        "low": round(float(row["Low"]), 4),
                        "close": round(float(row["Close"]), 4),
                        "volume": int(row["Volume"]),
                    }
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                continue
        return klines[-limit:]

    try:
        klines = await asyncio.to_thread(fetch)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Failed to fetch commodity historical data")

    data = {"symbol": sym, "interval": interval, "data": klines}
    _set_cache(cache_key, data, ttl=300)
    return data


@router.get("/news")
async def get_commodity_news(
    symbols: Optional[str] = None, limit: int = 15, lang: str = "zh-TW"
):
    """Recent news for commodities via yfinance."""
    if symbols:
        targets = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        targets = [c["symbol"] for c in DEFAULT_COMMODITIES[:5]]
    cache_key = f"news:{lang}:" + ",".join(targets)
    cached = _get_cache(cache_key)
    if cached:
        return cached
    news = await fetch_news(targets, limit_per=5, market="commodity", lang=lang)
    news = news[:limit]
    data = {"data": news, "last_updated": datetime.now(timezone.utc).isoformat()}
    if news:
        _set_cache(cache_key, data, ttl=600)
    return data
