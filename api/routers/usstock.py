import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api.deps import get_optional_current_user
from api.middleware.rate_limit import limiter
from api.routers._ttl_cache import TTLCache, swr
from api.user_llm import resolve_user_llm_credentials
from api.utils import logger

# Unified Provider 層（取代舊 us_data_provider）
from core.providers import get_provider as get_unified_provider
from utils.lazy_import import lazy_module

yf = lazy_module("yfinance")  # 第一次用到才載入（見 utils/lazy_import.py）

router = APIRouter(prefix="/api/usstock", tags=["US Stock"])

# ── Optional Finnhub API key (free commercial use: https://finnhub.io) ─────────
# Set FINNHUB_API_KEY in .env to use Finnhub as primary quote source (legal).
# Falls back to yfinance if key is absent.
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache = TTLCache(default_ttl=300)
_get_cache = _cache.get
_set_cache = _cache.set


# ── Constants ──────────────────────────────────────────────────────────────────
DEFAULT_US_SYMBOLS = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "TSLA",
    "META",
    "NVDA",
    "NFLX",
    "AMD",
    "INTC",
]
INDEX_SYMBOLS = [
    {"symbol": "^DJI", "name": "道瓊工業指數"},
    {"symbol": "^GSPC", "name": "S&P 500"},
    {"symbol": "^IXIC", "name": "那斯達克"},
]

# Yahoo Finance headers (for search endpoint)
_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# Static name table for curated symbols (avoids slow yfinance .info calls)
# US names are English-only: zh = en = English name
_US_STOCK_NAMES: dict[str, dict] = {
    # 科技
    "AAPL": {"zh": "Apple", "en": "Apple"},
    "MSFT": {"zh": "Microsoft", "en": "Microsoft"},
    "GOOGL": {"zh": "Alphabet", "en": "Alphabet"},
    "AMZN": {"zh": "Amazon", "en": "Amazon"},
    "META": {"zh": "Meta", "en": "Meta"},
    "NVDA": {"zh": "NVIDIA", "en": "NVIDIA"},
    "TSLA": {"zh": "Tesla", "en": "Tesla"},
    "NFLX": {"zh": "Netflix", "en": "Netflix"},
    "AMD": {"zh": "AMD", "en": "AMD"},
    "INTC": {"zh": "Intel", "en": "Intel"},
    "QCOM": {"zh": "Qualcomm", "en": "Qualcomm"},
    "AVGO": {"zh": "Broadcom", "en": "Broadcom"},
    "TSM": {"zh": "TSMC ADR", "en": "TSMC ADR"},
    "ORCL": {"zh": "Oracle", "en": "Oracle"},
    "CRM": {"zh": "Salesforce", "en": "Salesforce"},
    # 金融
    "JPM": {"zh": "JPMorgan", "en": "JPMorgan"},
    "BAC": {"zh": "Bank of America", "en": "Bank of America"},
    "GS": {"zh": "Goldman Sachs", "en": "Goldman Sachs"},
    "WFC": {"zh": "Wells Fargo", "en": "Wells Fargo"},
    "V": {"zh": "Visa", "en": "Visa"},
    "MA": {"zh": "Mastercard", "en": "Mastercard"},
    # 消費
    "WMT": {"zh": "Walmart", "en": "Walmart"},
    "COST": {"zh": "Costco", "en": "Costco"},
    "HD": {"zh": "Home Depot", "en": "Home Depot"},
    "NKE": {"zh": "Nike", "en": "Nike"},
    "MCD": {"zh": "McDonald's", "en": "McDonald's"},
    "SBUX": {"zh": "Starbucks", "en": "Starbucks"},
    # 醫療
    "JNJ": {"zh": "J&J", "en": "J&J"},
    "UNH": {"zh": "UnitedHealth", "en": "UnitedHealth"},
    "PFE": {"zh": "Pfizer", "en": "Pfizer"},
    "ABBV": {"zh": "AbbVie", "en": "AbbVie"},
    "MRK": {"zh": "Merck", "en": "Merck"},
    # 能源
    "XOM": {"zh": "ExxonMobil", "en": "ExxonMobil"},
    "CVX": {"zh": "Chevron", "en": "Chevron"},
    # ETF
    "SPY": {"zh": "S&P 500 ETF", "en": "S&P 500 ETF"},
    "QQQ": {"zh": "Nasdaq ETF", "en": "Nasdaq ETF"},
    "DIA": {"zh": "Dow Jones ETF", "en": "Dow Jones ETF"},
    "GLD": {"zh": "Gold ETF", "en": "Gold ETF"},
    "IWM": {"zh": "Russell 2000 ETF", "en": "Russell 2000 ETF"},
}


# ── Helpers ────────────────────────────────────────────────────────────────────
def _fetch_quote_sync(symbol: str) -> dict | None:
    """Fetch a single quote synchronously via unified Provider (fallback for Finnhub batch)."""
    try:
        provider = get_unified_provider("us")
        data = provider.get_price(symbol)
        if "error" in data or data.get("price") is None:
            return None
        # 補上 US-specific 中英文名稱（router 自有 curated dict）
        names = _US_STOCK_NAMES.get(symbol)
        if names:
            name_zh = names["zh"]
            name_en = names["en"]
        else:
            name_zh = data.get("name", symbol)
            name_en = data.get("name", symbol)
        return {
            "symbol": symbol,
            "name": name_zh,  # backward compat
            "name_zh": name_zh,
            "name_en": name_en,
            "price": data["price"],
            "change": data.get("change", 0),
            "changePercent": data.get("changePercent", 0),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[usstock] Provider quote failed {symbol}: {e}")
        return None


async def _fetch_quotes_finnhub(symbols: list[str]) -> list[dict]:
    """Fetch quotes from Finnhub API (primary, legal, free 60 req/min).
    Returns only successfully fetched results; caller falls back to yfinance."""
    results = []
    async with httpx.AsyncClient(timeout=10) as client:
        tasks = [
            client.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": s, "token": FINNHUB_API_KEY},
            )
            for s in symbols
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
    for sym, resp in zip(symbols, responses):
        try:
            if isinstance(resp, Exception):
                raise resp
            resp.raise_for_status()
            d = resp.json()
            price = round(float(d["c"]), 2)
            prev = round(float(d["pc"]), 2)
            if price == 0:
                continue  # Finnhub returns 0 for unknown symbols
            chg = round(price - prev, 2)
            chg_p = round((chg / prev) * 100, 2) if prev else 0.0
            names = _US_STOCK_NAMES.get(sym)
            if names:
                name_zh = names["zh"]
                name_en = names["en"]
            else:
                name_zh = sym
                name_en = sym
            results.append(
                {
                    "symbol": sym,
                    "name": name_zh,  # backward compat
                    "name_zh": name_zh,
                    "name_en": name_en,
                    "price": price,
                    "change": chg,
                    "changePercent": chg_p,
                }
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[usstock] Finnhub quote failed {sym}: {e}")
    return results


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/market")
async def get_us_market(symbols: Optional[str] = None):
    """Current price data for watchlist symbols."""
    target = (
        [s.strip().upper() for s in symbols.split(",")]
        if symbols
        else DEFAULT_US_SYMBOLS
    )
    cache_key = "market:" + ",".join(target)
    # 過期 10 分鐘內先回舊報價、背景更新（見 _ttl_cache.swr）；更舊就同步抓
    return await swr(_cache, cache_key, lambda: _compute_us_market(target), max_stale=600)


async def _compute_us_market(target: list) -> dict:
    if FINNHUB_API_KEY:
        stocks = await _fetch_quotes_finnhub(target)
        # Fallback to yfinance for any symbols Finnhub couldn't return
        fetched = {s["symbol"] for s in stocks}
        missing = [s for s in target if s not in fetched]
        if missing:
            fallback = await asyncio.gather(
                *[asyncio.to_thread(_fetch_quote_sync, s) for s in missing]
            )
            stocks += [r for r in fallback if r]
    else:
        results = await asyncio.gather(
            *[asyncio.to_thread(_fetch_quote_sync, s) for s in target]
        )
        stocks = [r for r in results if r]

    if not stocks and target:
        raise HTTPException(status_code=404, detail="Stock symbol not found or data is currently unavailable")
    return {"stocks": stocks, "last_updated": datetime.now(timezone.utc).isoformat()}


@router.get("/indices")
async def get_us_indices():
    """Current data for major US market indices."""
    return await swr(_cache, "indices", _compute_us_indices, ttl=60, max_stale=600)


async def _compute_us_indices() -> dict:
    async def fetch_index(idx):
        result = await asyncio.to_thread(_fetch_quote_sync, idx["symbol"])
        if result:
            result["name"] = idx["name"]
        return result

    results = await asyncio.gather(*[fetch_index(i) for i in INDEX_SYMBOLS])
    indices = [r for r in results if r]
    return {"indices": indices, "last_updated": datetime.now(timezone.utc).isoformat()}


@router.get("/news")
async def get_us_news(symbols: Optional[str] = None, limit: int = 15):
    """Recent news for given symbols (or top symbols if none specified)."""
    target = (
        [s.strip().upper() for s in symbols.split(",")]
        if symbols
        else DEFAULT_US_SYMBOLS[:5]
    )
    cache_key = "news:" + ",".join(target)
    # 新聞過期 1 小時內先回舊的、背景更新
    return await swr(
        _cache, cache_key, lambda: _compute_us_news(target, limit), ttl=300, max_stale=3600
    )


async def _compute_us_news(target: list, limit: int) -> dict:
    provider = get_unified_provider("us")
    all_news = []
    seen_titles = set()

    async def fetch_news_for(sym):
        try:
            items = await asyncio.to_thread(provider.get_news, sym, 5)
            return sym, items
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return sym, []

    results = await asyncio.gather(*[fetch_news_for(s) for s in target])
    for sym, items in results:
        for item in items or []:
            title = item.get("title", "")
            if title and title not in seen_titles:
                seen_titles.add(title)
                all_news.append(
                    {
                        "symbol": sym,
                        "title": title,
                        "url": item.get("url") or item.get("link", "#"),
                        "publisher": item.get("source") or item.get("publisher", ""),
                        "published": item.get("published_at")
                        or item.get("providerPublishTime")
                        or item.get("published", ""),
                    }
                )

    # Sort by publish time descending (best-effort)
    all_news.sort(key=lambda x: x.get("published", 0), reverse=True)
    all_news = all_news[:limit]
    return {"data": all_news, "last_updated": datetime.now(timezone.utc).isoformat()}


@limiter.limit("20/minute")



@router.get("/pulse/{symbol}")
async def get_us_pulse(
    request: Request,
    symbol: str,
    deep_analysis: bool = False,
    force_refresh: bool = False,
    lang: str = "zh-TW",
    x_user_llm_provider: Optional[str] = Header(None),
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """Deep AI pulse analysis for a single US stock."""
    sym = symbol.upper()
    cache_key = f"pulse:{sym}"
    if not deep_analysis:
        cached = _get_cache(cache_key)
        if cached:
            return cached

    # 統一走 core.providers.USProvider（sync 介面，asyncio.to_thread 並行）
    provider = get_unified_provider("us")

    try:
        price_data, tech_data, fund_data, news_data = await asyncio.gather(
            asyncio.to_thread(provider.get_price, sym),
            asyncio.to_thread(provider.get_technicals, sym),
            asyncio.to_thread(provider.get_fundamentals, sym),
            asyncio.to_thread(provider.get_news, sym, 3),
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[usstock] pulse failed for {sym}: {e}")
        raise HTTPException(
            status_code=404, detail=f"US stock symbol \"{sym}\" not found or data is currently unavailable"
        )

    curr_price = price_data.get("price", 0)
    change_pct = round(price_data.get("change_percent", 0), 2)
    company = price_data.get("name") or sym

    # 不顯示 fallback summary — 只有 deep_analysis 才有內容
    summary = None

    key_points = [
        f"RSI(14): {tech_data.get('rsi', 'N/A')}",
        f"MACD: {tech_data.get('macd', {}).get('histogram', 'N/A') if isinstance(tech_data.get('macd'), dict) else tech_data.get('macd', 'N/A')}",
        f"P/E Ratio: {fund_data.get('pe_ratio', 'N/A')}",
        f"EPS: {fund_data.get('eps', 'N/A')}",
        f"Market Cap: {fund_data.get('market_cap', 'N/A')}",
        f"52W High: {price_data.get('fifty_two_week_high', 'N/A')}",
        f"52W Low: {price_data.get('fifty_two_week_low', 'N/A')}",
        f"Volume: {price_data.get('volume', 'N/A')}",
    ]

    highlights = [
        {"title": n.get("title", ""), "url": n.get("url") or n.get("link", "#")}
        for n in (news_data or [])[:3]
        if n.get("title")
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
        if news_data:
            headlines = "\n".join(
                f"- {n['title']}" for n in news_data[:3] if n.get("title")
            )
            if headlines:
                news_str = f"\n近期新聞:\n{headlines}"

        context = (
            f"公司: {company} ({sym})\n"
            f"現價: ${curr_price}\n"
            f"24H 漲跌幅: {change_pct:+.2f}%\n"
            f"RSI(14): {tech_data.get('rsi', 'N/A')}\n"
            f"MACD: {tech_data.get('macd', {}).get('histogram', 'N/A') if isinstance(tech_data.get('macd'), dict) else tech_data.get('macd', 'N/A')}\n"
            f"MA20: {tech_data.get('ma_20', 'N/A')}\n"
            f"MA50: {tech_data.get('ma_50', 'N/A')}\n"
            f"P/E Ratio: {fund_data.get('pe_ratio', 'N/A')}\n"
            f"市值: {fund_data.get('market_cap', 'N/A')}\n"
            f"52W High: {price_data.get('fifty_two_week_high', 'N/A')}\n"
            f"52W Low: {price_data.get('fifty_two_week_low', 'N/A')}"
            f"{news_str}"
        )
        ai_result = await get_deep_analysis(
            market="usstock",
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
        "company_name": company,
        "current_price": curr_price,
        "change_24h": change_pct,
        "source_mode": source_mode,
        "ai_error": ai_error,
        "report": {
            "summary": summary,
            "key_points": key_points,
            "highlights": highlights,
        },
        "technical_indicators": tech_data,
        "fundamentals": fund_data,
        "cached_at": cached_at,
        "cache_expires_at": cache_expires_at,
        "cooldown_remaining": cooldown_remaining,
    }
    if not deep_analysis:
        _set_cache(cache_key, result, ttl=300)
    return result


@limiter.limit("30/minute")



@router.get("/klines/{symbol}")
async def get_us_klines(request: Request, symbol: str, interval: str = "1d", limit: int = 200):
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
                        "open": round(float(row["Open"]), 2),
                        "high": round(float(row["High"]), 2),
                        "low": round(float(row["Low"]), 2),
                        "close": round(float(row["Close"]), 2),
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
        raise HTTPException(status_code=404, detail="Failed to fetch stock data")

    data = {"symbol": sym, "interval": interval, "data": klines}
    _set_cache(cache_key, data, ttl=300)
    return data


@router.get("/search")
async def search_us_stocks(q: str):
    """Search US stocks via Yahoo Finance search API."""
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
        _us_exchanges = {"NMS", "NYQ", "NGM", "NCM", "PCX", "ASE"}
        results = [
            {
                "symbol": q["symbol"],
                "name": q.get("shortname") or q.get("longname") or q["symbol"],
            }
            for q in quotes
            if "." not in q.get("symbol", "")
            and q.get("quoteType") == "EQUITY"
            and q.get("exchange") in _us_exchanges
        ]
        return {"results": results}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[usstock] search failed: {e}")
        return {"results": []}
