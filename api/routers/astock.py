"""
A-Share Market Router — 陸股（滬深 A 股）
Data source: yfinance library (handles Yahoo Finance cookie/crumb automatically)
Symbols: 600xxx.SS (Shanghai), 000xxx/300xxx.SZ (Shenzhen)
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

router = APIRouter(prefix="/api/astock", tags=["A Stock"])

MARKET_DATA_UNAVAILABLE_MESSAGE = "A-share market data is currently unavailable; empty data returned for safe frontend fallback"

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache = TTLCache(default_ttl=300)
_get_cache = _cache.get
_set_cache = _cache.set


# ── Yahoo Finance headers (kept for search endpoint only) ─────────────────────
_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ── Curated A-share name table ─────────────────────────────────────────────────
_A_STOCK_NAMES: dict[str, dict] = {
    # 消費 / 白酒
    "600519.SS": {"zh": "貴州茅台", "en": "Kweichow Moutai"},
    "000858.SZ": {"zh": "五糧液", "en": "Wuliangye"},
    "600036.SS": {"zh": "招商銀行", "en": "China Merchants Bank"},
    # 金融 / 銀行
    "601318.SS": {"zh": "中國平安", "en": "Ping An"},
    "600000.SS": {"zh": "浦發銀行", "en": "SPDB"},
    "601398.SS": {"zh": "工商銀行", "en": "ICBC"},
    "601288.SS": {"zh": "農業銀行", "en": "ABC"},
    "600016.SS": {"zh": "民生銀行", "en": "CMBC"},
    # 科技 / 半導體
    "688981.SS": {"zh": "中芯國際", "en": "SMIC"},
    "002594.SZ": {"zh": "比亞迪", "en": "BYD"},
    "300750.SZ": {"zh": "寧德時代", "en": "CATL"},
    "601012.SS": {"zh": "隆基綠能", "en": "LONGi"},
    "600276.SS": {"zh": "恒瑞醫藥", "en": "Hengrui"},
    # 互聯網 / 科技
    "002475.SZ": {"zh": "立訊精密", "en": "Luxshare"},
    "300059.SZ": {"zh": "東方財富", "en": "East Money"},
    # 能源
    "600028.SS": {"zh": "中國石化", "en": "Sinopec"},
    "601857.SS": {"zh": "中國石油", "en": "PetroChina"},
    "600941.SS": {"zh": "中國移動", "en": "China Mobile"},
    # 地產
    "000002.SZ": {"zh": "萬科A", "en": "Vanke"},
    "600048.SS": {"zh": "保利發展", "en": "Poly Developments"},
    # 食品 / 消費
    "600887.SS": {"zh": "伊利股份", "en": "Yili"},
    "603288.SS": {"zh": "海天味業", "en": "Haitian Flavoring"},
    # 醫療
    "300015.SZ": {"zh": "愛爾眼科", "en": "Aier Eye"},
    "600196.SS": {"zh": "復星醫藥", "en": "Fosun Pharma"},
    # 前端可選清單裡有、這張表原本漏掉的（新聞搜尋詞要靠中文名）
    "000333.SZ": {"zh": "美的集團", "en": "Midea"},
    "600030.SS": {"zh": "中信證券", "en": "CITIC Securities"},
    "600900.SS": {"zh": "長江電力", "en": "China Yangtze Power"},
    "601166.SS": {"zh": "興業銀行", "en": "Industrial Bank"},
    "601899.SS": {"zh": "紫金礦業", "en": "Zijin Mining"},
    # Indices
    "000001.SS": {"zh": "上證指數", "en": "SSE Composite"},
    "399001.SZ": {"zh": "深證成指", "en": "SZSE Component"},
    "399006.SZ": {"zh": "創業板指", "en": "ChiNext Index"},
    # ETF
    "510300.SS": {"zh": "滬深300ETF", "en": "CSI 300 ETF"},
    "510500.SS": {"zh": "中證500ETF", "en": "CSI 500 ETF"},
    "159915.SZ": {"zh": "創業板ETF", "en": "ChiNext ETF"},
}

DEFAULT_A_SYMBOLS = [
    "600519.SS",
    "000858.SZ",
    "600036.SS",
    "601318.SS",
    "002594.SZ",
    "300750.SZ",
    "600028.SS",
    "510300.SS",
]


# ── Quote / Technicals — 改用 yfinance（自動處理 cookie/crumb）────────────────


async def _fetch_quotes_batch(symbols: list[str]) -> list[dict]:
    """Fetch quotes via yfinance (handles Yahoo Finance auth automatically)."""
    return await fetch_quotes(
        symbols, _A_STOCK_NAMES, decimal_places=2, default_currency="CNY"
    )


async def _fetch_technicals(symbol: str) -> dict:
    """Fetch RSI(14) and 52w high/low via yfinance."""
    return await asyncio.to_thread(fetch_technicals_sync, symbol, 2)


def _fetch_extras_sync(symbol: str) -> dict:
    """Fetch fundamentals via yfinance (shared implementation from yf_helpers)."""
    from api.routers.yf_helpers import fetch_extras_sync

    return fetch_extras_sync(symbol)


async def _fetch_extras(symbol: str) -> dict:
    from api.routers.yf_helpers import fetch_extras

    return await fetch_extras(symbol)


# ── Endpoints ───────────────────────────────────────────────────────────────────


@router.get("/market")
async def get_a_market(symbols: Optional[str] = None):
    """Current price data for A-share stocks."""
    targets = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else DEFAULT_A_SYMBOLS
    )
    cache_key = "market:" + ",".join(targets)
    cached = _get_cache(cache_key)
    if cached:
        return cached

    quotes = await _fetch_quotes_batch(targets)
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
async def get_a_pulse(
    request: Request,
    symbol: str,
    deep_analysis: bool = False,
    force_refresh: bool = False,
    lang: str = "zh-TW",
    x_user_llm_provider: Optional[str] = Header(None),
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """Market pulse analysis for a single A-share stock."""
    sym = symbol.upper()
    # Normalise: accept 600519 → 600519.SS, 000858 → 000858.SZ
    if "." not in sym:
        if sym.startswith("6") or sym.startswith("9"):
            sym = sym + ".SS"
        else:
            sym = sym + ".SZ"

    cache_key = f"pulse:{sym}"
    if not deep_analysis:
        cached = _get_cache(cache_key)
        if cached:
            return cached

    quotes, tech, news_items, extras = await asyncio.gather(
        _fetch_quotes_batch([sym]),
        _fetch_technicals(sym),
        fetch_news([sym], limit_per=5, market="cn", names=_A_STOCK_NAMES, lang=lang),
        _fetch_extras(sym),
    )
    if not quotes:
        raise HTTPException(
            status_code=404, detail=f"A-share symbol \"{sym}\" not found or data is currently unavailable"
        )

    q = quotes[0]
    price = q["price"]
    chg_p = q["changePercent"]
    display_name = q["name"]
    currency = q.get("currency", "CNY")

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
    if deep_analysis:
        credentials = await resolve_user_llm_credentials(
            current_user, x_user_llm_provider
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
            f"MARKET_LABEL: A 股 ({sym})\n"
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
            market="astock",
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
        # key_points only shown when no deep analysis (structured tech card replaces them)
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
async def get_a_klines(request: Request, symbol: str, interval: str = "1d", limit: int = 200):
    """Historical OHLCV kline data via yfinance."""
    sym = symbol.upper()
    if "." not in sym:
        sym = sym + ".SS" if sym.startswith("6") or sym.startswith("9") else sym + ".SZ"

    cache_key = f"klines:{sym}:{interval}"
    cached = _get_cache(cache_key)
    if cached:
        return cached

    try:
        klines = await asyncio.to_thread(fetch_klines_sync, sym, interval, limit, 2)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Failed to fetch A-share historical data: {e}")

    result_data = {"symbol": sym, "interval": interval, "data": klines}
    _set_cache(cache_key, result_data, ttl=300)
    return result_data


@router.get("/search")
async def search_a_stocks(q: str):
    """Search A-share stocks via Yahoo Finance search API."""
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
            if (
                q.get("symbol", "").endswith(".SS")
                or q.get("symbol", "").endswith(".SZ")
            )
            and q.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND")
        ]
        return {"results": results}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[astock] search failed: {e}")
        return {"results": []}


@router.get("/news")
async def get_a_news(symbols: Optional[str] = None, limit: int = 15):
    """Recent news for A-share stocks via yfinance."""
    targets = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else DEFAULT_A_SYMBOLS[:5]
    )
    cache_key = "news:" + ",".join(targets)
    cached = _get_cache(cache_key)
    if cached:
        return cached
    news = await fetch_news(targets, limit_per=5, market="cn", names=_A_STOCK_NAMES)
    news = news[:limit]
    data = {"data": news, "last_updated": datetime.now(timezone.utc).isoformat()}
    if news:
        _set_cache(cache_key, data, ttl=600)
    return data
