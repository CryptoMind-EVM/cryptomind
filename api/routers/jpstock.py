"""
JP Stock Market Router — 日股市場
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

router = APIRouter(prefix="/api/jpstock", tags=["JP Stock"])

MARKET_DATA_UNAVAILABLE_MESSAGE = "目前無法取得日股行情，已回傳空資料供前端安全降級"

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache = TTLCache(default_ttl=300)
_get_cache = _cache.get
_set_cache = _cache.set


# ── Yahoo Finance headers (kept for search endpoint only) ─────────────────────
_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ── Curated JP stock name table ────────────────────────────────────────────────
# "local" = 日文名稱，只用來組 Google News 的搜尋詞（日本媒體用日文寫，英文名查不到，見 news_feeds.py）
# 日本沒有 Yahoo 查得到的 TOPIX 指數代號（^TOPX／^TPX／998405.T 實測都查無資料），
# TOPIX 以 TOPIX 連動 ETF 1306.T 代表。
_JP_STOCK_NAMES: dict[str, dict] = {
    # Indices
    "^N225": {"zh": "日經225", "en": "Nikkei 225", "local": "日経平均"},
    # 汽車
    "7203.T": {"zh": "豐田汽車", "en": "Toyota", "local": "トヨタ自動車"},
    "7267.T": {"zh": "本田", "en": "Honda", "local": "ホンダ"},
    "7201.T": {"zh": "日產", "en": "Nissan", "local": "日産自動車"},
    "7269.T": {"zh": "鈴木", "en": "Suzuki", "local": "スズキ"},
    "7270.T": {"zh": "速霸陸", "en": "Subaru", "local": "SUBARU"},
    # 科技 / 電子
    "6758.T": {"zh": "索尼", "en": "Sony", "local": "ソニーグループ"},
    "6861.T": {"zh": "基恩士", "en": "Keyence", "local": "キーエンス"},
    "6501.T": {"zh": "日立", "en": "Hitachi", "local": "日立製作所"},
    "6752.T": {"zh": "松下", "en": "Panasonic", "local": "パナソニック"},
    "6702.T": {"zh": "富士通", "en": "Fujitsu", "local": "富士通"},
    "6723.T": {"zh": "瑞薩電子", "en": "Renesas", "local": "ルネサスエレクトロニクス"},
    "6367.T": {"zh": "大金工業", "en": "Daikin", "local": "ダイキン工業"},
    "7974.T": {"zh": "任天堂", "en": "Nintendo", "local": "任天堂"},
    # 半導體 / 設備
    "8035.T": {"zh": "東京威力科創", "en": "TEL", "local": "東京エレクトロン"},
    "4063.T": {"zh": "信越化學", "en": "Shin-Etsu", "local": "信越化学工業"},
    # 金融
    "8306.T": {"zh": "三菱UFJ銀行", "en": "MUFG", "local": "三菱UFJフィナンシャル・グループ"},
    "8316.T": {"zh": "三井住友", "en": "SMFG", "local": "三井住友フィナンシャルグループ"},
    "8411.T": {"zh": "瑞穗銀行", "en": "Mizuho", "local": "みずほフィナンシャルグループ"},
    "8604.T": {"zh": "野村控股", "en": "Nomura", "local": "野村ホールディングス"},
    # 零售 / 消費
    "9983.T": {"zh": "迅銷", "en": "Fast Retailing (Uniqlo)", "local": "ファーストリテイリング"},
    "2914.T": {"zh": "日本菸草", "en": "JT", "local": "日本たばこ産業"},
    "2802.T": {"zh": "味之素", "en": "Ajinomoto", "local": "味の素"},
    "2503.T": {"zh": "麒麟", "en": "Kirin", "local": "キリンホールディングス"},
    # 電信 / 投資
    "9432.T": {"zh": "NTT", "en": "NTT", "local": "NTT"},
    "9433.T": {"zh": "KDDI", "en": "KDDI", "local": "KDDI"},
    "9434.T": {"zh": "軟銀", "en": "SoftBank", "local": "ソフトバンク"},
    "9984.T": {"zh": "軟銀集團", "en": "SoftBank Group", "local": "ソフトバンクグループ"},
    # 醫療 / 製藥
    "4502.T": {"zh": "武田藥品", "en": "Takeda", "local": "武田薬品工業"},
    "4519.T": {"zh": "中外製藥", "en": "Chugai", "local": "中外製薬"},
    # ETF
    "1306.T": {"zh": "TOPIX ETF (野村)", "en": "TOPIX ETF (Nomura)", "local": "TOPIX"},
}

DEFAULT_JP_SYMBOLS = [
    "7203.T",
    "6758.T",
    "8306.T",
    "9983.T",
    "8035.T",
    "9432.T",
    "4502.T",
    "1306.T",
]

# ── Normalise symbol: accept 7203 → 7203.T ────────────────────────────────────


def _normalise(symbol: str) -> str:
    s = symbol.upper()
    # Indices like ^N225 — skip .T suffix
    if s.startswith("^"):
        return s
    if not s.endswith(".T"):
        s = s + ".T"
    return s


# ── Quote / Technicals — 改用 yfinance（自動處理 cookie/crumb）────────────────


async def _fetch_quotes_yahoo_batch(symbols: list[str]) -> list[dict]:
    """Fetch quotes via yfinance (handles Yahoo Finance auth automatically)."""
    return await fetch_quotes(
        symbols, _JP_STOCK_NAMES, decimal_places=1, default_currency="JPY"
    )


async def _fetch_technicals_yahoo(symbol: str) -> dict:
    """Fetch RSI(14) and 52w high/low via yfinance."""
    return await asyncio.to_thread(fetch_technicals_sync, symbol, 1)


def _fetch_extras_sync(symbol: str) -> dict:
    """Fetch fundamentals via yfinance (shared implementation from yf_helpers)."""
    from api.routers.yf_helpers import fetch_extras_sync

    return fetch_extras_sync(symbol)


async def _fetch_extras(symbol: str) -> dict:
    from api.routers.yf_helpers import fetch_extras

    return await fetch_extras(symbol)


# ── Endpoints ───────────────────────────────────────────────────────────────────


@router.get("/market")
async def get_jp_market(symbols: Optional[str] = None):
    """Current price data for JP stocks."""
    targets = (
        [_normalise(s.strip()) for s in symbols.split(",") if s.strip()]
        if symbols
        else DEFAULT_JP_SYMBOLS
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

    # 只 cache 有資料的結果，避免空結果被 cache 5 分鐘
    if quotes:
        _set_cache(cache_key, data)
    return data


@limiter.limit("20/minute")



@router.get("/pulse/{symbol}")
async def get_jp_pulse(
    request: Request,
    symbol: str,
    deep_analysis: bool = False,
    force_refresh: bool = False,
    lang: str = "zh-TW",
    x_user_llm_provider: Optional[str] = Header(None),
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """Market pulse analysis for a single JP stock."""
    sym = _normalise(symbol)

    cache_key = f"pulse:{sym}"
    if not deep_analysis:
        cached = _get_cache(cache_key)
        if cached:
            return cached

    quotes, tech, news_items, extras = await asyncio.gather(
        _fetch_quotes_yahoo_batch([sym]),
        _fetch_technicals_yahoo(sym),
        fetch_news([sym], limit_per=5, market="jp", names=_JP_STOCK_NAMES, lang=lang),
        _fetch_extras(sym),
    )
    if not quotes:
        raise HTTPException(
            status_code=404, detail=f"Japanese stock symbol \"{sym}\" not found or data is currently unavailable"
        )

    q = quotes[0]
    price = q["price"]
    chg_p = q["changePercent"]
    display_name = q["name"]
    currency = q.get("currency", "JPY")

    trend_str = "走升" if chg_p > 0 else ("走跌" if chg_p < 0 else "持平")
    rsi = tech.get("rsi")
    # 算不出來的指標是 None：要點與 prompt 一律顯示 N/A（不是「None」）
    tech_text = technicals_for_display(tech)
    rsi_str = ""
    if isinstance(rsi, (int, float)):
        if rsi > 70:
            rsi_str = "，RSI 顯示可能處於超買區間"
        elif rsi < 30:
            rsi_str = "，RSI 顯示可能處於超賣區間"
        else:
            rsi_str = "，RSI 落在中性區間"

    summary = (
        f"{display_name} ({sym}) 目前報價為 {price:,.0f} {currency}，"
        f"24小時{trend_str} ({chg_p:+.2f}%){rsi_str}。"
    )
    key_points = [
        f"RSI(14): {tech_text.get('rsi', 'N/A')}",
        f"MACD Histogram: {tech_text.get('macd_histogram', 'N/A')}",
        f"MA20: {tech_text.get('ma20', 'N/A'):,} {currency}"
        if isinstance(tech.get("ma20"), (int, float))
        else "MA20: N/A",
        f"MA50: {tech_text.get('ma50', 'N/A'):,} {currency}"
        if isinstance(tech.get("ma50"), (int, float))
        else "MA50: N/A",
        f"52W High: {tech_text.get('52w_high', 'N/A'):,} {currency}"
        if isinstance(tech.get("52w_high"), (int, float))
        else "52W High: N/A",
        f"52W Low: {tech_text.get('52w_low', 'N/A'):,} {currency}"
        if isinstance(tech.get("52w_low"), (int, float))
        else "52W Low: N/A",
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
            f"MARKET_LABEL: 日股 ({sym})\n"
            f"現價: {price:,.0f} {currency}\n"
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
            market="jpstock",
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
async def get_jp_klines(request: Request, symbol: str, interval: str = "1d", limit: int = 200):
    """Historical OHLCV kline data via yfinance."""
    sym = _normalise(symbol)

    cache_key = f"klines:{sym}:{interval}"
    cached = _get_cache(cache_key)
    if cached:
        return cached

    try:
        klines = await asyncio.to_thread(fetch_klines_sync, sym, interval, limit, 1)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Failed to fetch Japanese stock historical data: {e}")

    result_data = {"symbol": sym, "interval": interval, "data": klines}
    _set_cache(cache_key, result_data, ttl=300)
    return result_data


@router.get("/search")
async def search_jp_stocks(q: str):
    """Search Japanese stocks via Yahoo Finance search API."""
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
            if q.get("symbol", "").endswith(".T")
            and q.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND")
        ]
        return {"results": results}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[jpstock] search failed: {e}")
        return {"results": []}


@router.get("/news")
async def get_jp_news(symbols: Optional[str] = None, limit: int = 15):
    """Recent news for JP stocks via yfinance."""
    targets = (
        [_normalise(s.strip()) for s in symbols.split(",") if s.strip()]
        if symbols
        else DEFAULT_JP_SYMBOLS[:5]
    )
    cache_key = "news:" + ",".join(targets)
    cached = _get_cache(cache_key)
    if cached:
        return cached
    news = await fetch_news(targets, limit_per=5, market="jp", names=_JP_STOCK_NAMES)
    news = news[:limit]
    data = {"data": news, "last_updated": datetime.now(timezone.utc).isoformat()}
    if news:
        _set_cache(cache_key, data, ttl=600)
    return data
