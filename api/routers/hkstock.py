"""
HK Stock Market Router — 港股市場
Data source: yfinance library (handles Yahoo Finance cookie/crumb automatically)
Switched from direct HTTP calls to yfinance to avoid production server IP blocks.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api.deps import get_optional_current_user
from api.middleware.rate_limit import limiter
from api.routers._ttl_cache import TTLCache
from api.routers.yf_helpers import (
    fetch_klines_sync,
    fetch_news,
    fetch_quotes,
    fetch_technicals_sync,
    technicals_for_display,
)
from api.user_llm import resolve_user_llm_credentials
from api.utils import logger
from core.providers.base_provider import normalize_symbol

router = APIRouter(prefix="/api/hkstock", tags=["HK Stock"])

MARKET_DATA_UNAVAILABLE_MESSAGE = "HK market data is currently unavailable; empty data returned for safe frontend fallback"

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache = TTLCache(default_ttl=300)
_get_cache = _cache.get
_set_cache = _cache.set


# ── Yahoo Finance headers (kept for search endpoint only) ─────────────────────
_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ── Curated HK stock name table ────────────────────────────────────────────────
_HK_STOCK_NAMES: dict[str, dict] = {
    # 科技 / 互聯網
    "0700.HK": {"zh": "騰訊", "en": "Tencent"},
    "9988.HK": {"zh": "阿里巴巴", "en": "Alibaba"},
    "3690.HK": {"zh": "美團", "en": "Meituan"},
    "9618.HK": {"zh": "京東", "en": "JD.com"},
    "0992.HK": {"zh": "聯想", "en": "Lenovo"},
    "0241.HK": {"zh": "阿里健康", "en": "Alibaba Health"},
    # 金融
    "0005.HK": {"zh": "匯豐", "en": "HSBC"},
    "0939.HK": {"zh": "建設銀行", "en": "CCB"},
    "1398.HK": {"zh": "工商銀行", "en": "ICBC"},
    "3988.HK": {"zh": "中國銀行", "en": "Bank of China"},
    "2318.HK": {"zh": "中國平安", "en": "Ping An"},
    # 地產 / 基建
    "0016.HK": {"zh": "新鴻基地產", "en": "Sun Hung Kai"},
    "0001.HK": {"zh": "長和", "en": "CK Hutchison"},
    "0002.HK": {"zh": "中電控股", "en": "CLP Holdings"},
    "0003.HK": {"zh": "香港中華煤氣", "en": "Hong Kong Gas"},
    # 消費 / 零售
    "0291.HK": {"zh": "華潤啤酒", "en": "CR Beer"},
    "0027.HK": {"zh": "銀河娛樂", "en": "Galaxy Entertainment"},
    "1928.HK": {"zh": "金沙中國", "en": "Sands China"},
    # 交易所 / 指數相關
    "0388.HK": {"zh": "港交所", "en": "HKEX"},
    # 醫療 / 生物
    "1177.HK": {"zh": "中國生物製藥", "en": "Sino Biopharm"},
    "6160.HK": {"zh": "百濟神州", "en": "BeiGene"},
    # 汽車 / 新能源
    "0175.HK": {"zh": "吉利汽車", "en": "Geely"},
    "2015.HK": {"zh": "理想汽車", "en": "Li Auto"},
    "9866.HK": {"zh": "蔚來", "en": "NIO"},
    # ETF
    "2800.HK": {"zh": "盈富基金", "en": "Tracker Fund (HSI ETF)"},
    "3032.HK": {"zh": "恒生科技ETF", "en": "HS Tech ETF"},
}

DEFAULT_HK_SYMBOLS = [
    "0700.HK",
    "9988.HK",
    "0005.HK",
    "0388.HK",
    "3690.HK",
    "2318.HK",
    "0939.HK",
    "2800.HK",
]

# ── Quote / Technicals — 改用 yfinance（自動處理 cookie/crumb）────────────────


async def _fetch_quotes_yahoo_batch(symbols: list[str]) -> list[dict]:
    """Fetch quotes via yfinance (handles Yahoo Finance auth automatically)."""
    return await fetch_quotes(
        symbols, _HK_STOCK_NAMES, decimal_places=3, default_currency="HKD"
    )


async def _fetch_technicals_yahoo(symbol: str) -> dict:
    """Fetch RSI(14) and 52w high/low via yfinance."""
    return await asyncio.to_thread(fetch_technicals_sync, symbol, 3)


def _fetch_extras_sync(symbol: str) -> dict:
    """Fetch fundamentals via yfinance (shared implementation from yf_helpers)."""
    from api.routers.yf_helpers import fetch_extras_sync

    return fetch_extras_sync(symbol)


async def _fetch_extras(symbol: str) -> dict:
    from api.routers.yf_helpers import fetch_extras

    return await fetch_extras(symbol)


# ── Endpoints ───────────────────────────────────────────────────────────────────


@router.get("/market")
async def get_hk_market(symbols: Optional[str] = None):
    """Current price data for HK stocks."""
    targets = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else DEFAULT_HK_SYMBOLS
    )
    cache_key = "market:" + ",".join(targets)
    cached = _get_cache(cache_key)
    if cached:
        return cached

    quotes = await _fetch_quotes_yahoo_batch(targets)
    data = {
        "quotes": quotes,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    if len(quotes) != len(targets):
        data["partial_failure"] = True
        data["warning"] = MARKET_DATA_UNAVAILABLE_MESSAGE

    _set_cache(cache_key, data)
    return data


@limiter.limit("20/minute")



@router.get("/pulse/{symbol}")
async def get_hk_pulse(
    request: Request,
    symbol: str,
    deep_analysis: bool = False,
    force_refresh: bool = False,
    lang: str = "zh-TW",
    x_user_llm_provider: Optional[str] = Header(None),
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """Market pulse analysis for a single HK stock."""
    # Normalise: accept 700 → 0700.HK, 700.HK → 0700.HK
    sym = normalize_symbol(symbol, "hk")

    cache_key = f"pulse:{sym}"
    if not deep_analysis:
        cached = _get_cache(cache_key)
        if cached:
            return cached

    quotes, tech, news_items, extras = await asyncio.gather(
        _fetch_quotes_yahoo_batch([sym]),
        _fetch_technicals_yahoo(sym),
        fetch_news([sym], limit_per=5, market="hk", names=_HK_STOCK_NAMES, lang=lang),
        _fetch_extras(sym),
    )
    if not quotes:
        raise HTTPException(
            status_code=404, detail=f"HK stock symbol \"{sym}\" not found or data is currently unavailable"
        )

    q = quotes[0]
    price = q["price"]
    chg_p = q["changePercent"]
    display_name = q["name"]
    currency = q.get("currency", "HKD")

    trend_str = "rising" if chg_p > 0 else ("falling" if chg_p < 0 else "flat")
    rsi = tech.get("rsi")
    # 算不出來的指標是 None：要點與 prompt 一律顯示 N/A（不是「None」）
    tech_text = technicals_for_display(tech)
    rsi_str = ""
    if isinstance(rsi, (int, float)):
        if rsi > 70:
            rsi_str = "; RSI suggests it may be in overbought territory"
        elif rsi < 30:
            rsi_str = "; RSI suggests it may be in oversold territory"
        else:
            rsi_str = "; RSI is in neutral territory"

    summary = (
        f"{display_name} ({sym}) is currently trading at {price} {currency}, "
        f"24h {trend_str} ({chg_p:+.2f}%){rsi_str}."
    )
    key_points = [
        f"RSI(14): {tech_text.get('rsi', 'N/A')}",
        f"MACD Histogram: {tech_text.get('macd_histogram', 'N/A')}",
        f"MA20: {tech_text.get('ma20', 'N/A')} {currency}",
        f"MA50: {tech_text.get('ma50', 'N/A')} {currency}",
        f"52W High: {tech_text.get('52w_high', 'N/A')} {currency}",
        f"52W Low: {tech_text.get('52w_low', 'N/A')} {currency}",
        f"24H Change: {chg_p:+.2f}%",
    ]

    source_mode = "on_demand"
    credentials = None
    ai_error = None
    cached_at = None
    cache_expires_at = None
    cooldown_remaining = None
    logger.info(
        f"[hk_pulse] {sym} deep={deep_analysis} user={current_user and current_user.get('user_id')} hint={x_user_llm_provider}"
    )
    if deep_analysis:
        credentials = await resolve_user_llm_credentials(
            current_user, x_user_llm_provider
        )
        logger.info(
            f"[hk_pulse] creds={'found:' + credentials['provider'] if credentials else 'None'}"
        )
    if credentials:
        from api.routers.deep_analysis_helper import get_deep_analysis

        vol_str = f"{extras.get('volume', 0):,}" if extras.get("volume") else "N/A"
        avg_vol = extras.get("avg_volume")
        if avg_vol and extras.get("volume"):
            vol_ratio = extras["volume"] / avg_vol
            vol_context = f"{vol_str} (均量 {avg_vol:,}，比值 {vol_ratio:.1f}x)"
        else:
            vol_context = vol_str
        mc = extras.get("market_cap")
        if mc:
            if mc >= 1e12:
                mc_str = f"{mc / 1e12:.2f}兆 {currency}"
            elif mc >= 1e8:
                mc_str = f"{mc / 1e8:.0f}億 {currency}"
            else:
                mc_str = f"{mc:,} {currency}"
        else:
            mc_str = "N/A"
        pe_str = str(extras.get("pe_ratio", "N/A"))
        pb_str = str(extras.get("pb_ratio", "N/A"))
        beta_str = str(extras.get("beta", "N/A"))
        dy = extras.get("dividend_yield")
        dy_str = f"{dy:.2f}%" if dy is not None else "N/A"
        eps_str = str(extras.get("eps", "N/A"))
        rg = extras.get("revenue_growth")
        rg_str = f"{rg:+.1f}%" if rg is not None else "N/A"
        eg = extras.get("earnings_growth")
        eg_str = f"{eg:+.1f}%" if eg is not None else "N/A"
        pm = extras.get("profit_margins")
        pm_str = f"{pm:.1f}%" if pm is not None else "N/A"
        tp = extras.get("target_price")
        if tp and price:
            upside = (tp - price) / price * 100
            tp_str = f"{tp} ({upside:+.1f}%)"
        elif tp:
            tp_str = str(tp)
        else:
            tp_str = "N/A"
        rec_str = extras.get("recommendation", "").upper() or "N/A"
        sector_str = extras.get("sector") or "N/A"
        industry_str = extras.get("industry") or "N/A"

        news_str = ""
        if news_items:
            headlines = "\n".join(f"- {n['title']}" for n in news_items[:5])
            news_str = f"\n近期新聞（最新5條）:\n{headlines}"

        context = (
            f"港股: {display_name} ({sym})\n"
            f"現價: {price} {currency}\n"
            f"24H 漲跌幅: {chg_p:+.2f}%\n"
            f"成交量: {vol_context}\n"
            f"市值: {mc_str}　本益比(PE): {pe_str}　PB: {pb_str}\n"
            f"EPS(TTM): {eps_str}　股息殖利率: {dy_str}\n"
            f"營收成長: {rg_str}　獲利成長: {eg_str}　淨利率: {pm_str}\n"
            f"Beta(β): {beta_str}\n"
            f"第三方分析師預估目標價（非本平台預測）: {tp_str}　分析師建議: {rec_str}\n"
            f"所屬產業: {sector_str} / {industry_str}\n"
            f"RSI(14): {tech_text.get('rsi', 'N/A')}\n"
            f"MACD Histogram: {tech_text.get('macd_histogram', 'N/A')}\n"
            f"MA20: {tech_text.get('ma20', 'N/A')} {currency}\n"
            f"MA50: {tech_text.get('ma50', 'N/A')} {currency}\n"
            f"52W High: {tech_text.get('52w_high', 'N/A')} {currency}\n"
            f"52W Low: {tech_text.get('52w_low', 'N/A')} {currency}"
            f"{news_str}"
        )
        ai_result = await get_deep_analysis(
            market="hkstock",
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
        "currency": currency,
        "change_24h": chg_p,
        "source_mode": source_mode,
        "ai_error": ai_error,
        "report": {
            "summary": summary,
            "key_points": key_points if source_mode != "deep_analysis" else [],
        },
        "technical_indicators": tech,
        "fundamentals": extras,
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
async def get_hk_klines(request: Request, symbol: str, interval: str = "1d", limit: int = 200):
    """Historical OHLCV kline data via yfinance."""
    sym = normalize_symbol(symbol, "hk")

    cache_key = f"klines:{sym}:{interval}"
    cached = _get_cache(cache_key)
    if cached:
        return cached

    try:
        klines = await asyncio.to_thread(fetch_klines_sync, sym, interval, limit, 3)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Failed to fetch HK historical data: {e}")

    result_data = {"symbol": sym, "interval": interval, "data": klines}
    _set_cache(cache_key, result_data, ttl=300)
    return result_data


@router.get("/search")
async def search_hk_stocks(q: str):
    """Search HK stocks via Yahoo Finance search API."""
    if not q or len(q.strip()) < 1:
        return {"results": []}
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/search"
        params = {
            "q": q,
            "lang": "en-US",
            "region": "US",
            "quotesCount": 10,
            "newsCount": 0,
            "enableFuzzyQuery": False,
            "quotesQueryId": "tss_match_phrase_query",
        }
        async with httpx.AsyncClient(timeout=8, headers=_YF_HEADERS) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        quotes = data.get("quotes", [])
        results = [
            {
                "symbol": q["symbol"],
                "name": q.get("shortname") or q.get("longname") or q["symbol"],
            }
            for q in quotes
            if q.get("symbol", "").endswith(".HK")
            and q.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND")
        ]
        return {"results": results}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[hkstock] search failed: {e}")
        return {"results": []}


@router.get("/news")
async def get_hk_news(symbols: Optional[str] = None, limit: int = 15):
    """Recent news for HK stocks via yfinance."""
    targets = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else DEFAULT_HK_SYMBOLS[:5]
    )
    cache_key = "news:" + ",".join(targets)
    cached = _get_cache(cache_key)
    if cached:
        return cached
    news = await fetch_news(targets, limit_per=5, market="hk", names=_HK_STOCK_NAMES)
    news = news[:limit]
    data = {"data": news, "last_updated": datetime.now(timezone.utc).isoformat()}
    if news:
        _set_cache(cache_key, data, ttl=600)
    return data
