import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from api.deps import get_optional_current_user
from api.middleware.rate_limit import limiter
from api.routers._ttl_cache import schedule_refresh
from api.user_llm import resolve_user_llm_credentials
from api.utils import logger
from core.tools.tw_stock_tools import (
    tw_dividend_info,
    tw_fundamentals,
    tw_institutional,
    tw_monthly_revenue,
    tw_news,
    tw_stock_price,
    tw_technical_analysis,
)
from utils.lazy_import import lazy_module

yf = lazy_module("yfinance")  # 第一次用到才載入（見 utils/lazy_import.py）

# ── Simple in-memory cache (key → (data, expiry_time)) ──────────────────────
_twse_cache: dict = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


# TWSE 公開資料多為日／月資料：過期 24 小時內先回舊的、背景更新（見 _ttl_cache.swr）
_TWSE_MAX_STALE_SECONDS = 24 * 3600


async def _fetch_twse_now(url: str, params: Optional[dict], ck: str) -> Any:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        _twse_cache[ck] = (
            data,
            datetime.now(timezone.utc) + timedelta(seconds=_CACHE_TTL_SECONDS),
        )
        return data


async def _fetch_twse(url: str, params: dict = None, cache_key: str = None) -> Any:
    """Fetch from TWSE OpenAPI with optional caching."""
    ck = cache_key or url
    if ck in _twse_cache:
        data, expiry = _twse_cache[ck]
        now = datetime.now(timezone.utc)
        if now < expiry:
            return data
        if now - expiry < timedelta(seconds=_TWSE_MAX_STALE_SECONDS):
            schedule_refresh((id(_twse_cache), ck), lambda: _fetch_twse_now(url, params, ck))
            return data
    try:
        return await _fetch_twse_now(url, params, ck)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[TWSE fetch] {url} failed: {e}")
        raise


router = APIRouter(prefix="/api/twstock", tags=["TW Stock"])

# Default preset symbols for Taiwan Stocks (e.g., TSMC, Foxconn, MediaTek)
DEFAULT_TW_SYMBOLS = [
    "2330",
    "2317",
    "2454",
    "2308",
    "2881",
    "2412",
    "2882",
    "2891",
    "1301",
    "2002",
]

_STOCK_INFO_CACHE = {}  # {symbol: (result, expires_at)}


# ── Supplementary yfinance fundamentals (DRY: delegated to YahooFinanceProvider) ──
# 之前這裡有 ~45 行重複的 _fetch_yf_extras_sync，現已統一由 Provider 層處理。
# Provider 提供完整的基本面（含 beta/target_price/recommendation/sector/industry
# /revenue_growth/earnings_growth/avg_volume/PE/PB/EPS/股息率等）。
def _fetch_yf_extras_sync(symbol: str) -> dict:
    """Fetch supplementary fundamentals via unified YahooFinanceProvider."""
    from core.providers.yahoo_provider import YahooFinanceProvider

    # 直接用 yfinance 後綴的 symbol（如 '2330.TW'）；TW market 預設邏輯會處理
    return YahooFinanceProvider(market="tw").get_fundamentals(symbol)


async def _fetch_yf_extras(symbol: str) -> dict:
    return await asyncio.to_thread(_fetch_yf_extras_sync, symbol)


async def _get_stock_info(symbol: str):
    """
    獲取台股名稱及所屬交易所(處理.TW與.TWO)。
    This function caches the result to avoid slow repeated yfinance info calls.
    Successful lookups cached for 1 hour; fallback cached for 5 minutes.
    """
    symbol = symbol.replace(".TW", "").replace(".TWO", "")
    now = time.time()
    if symbol in _STOCK_INFO_CACHE:
        cached_result, expires_at = _STOCK_INFO_CACHE[symbol]
        if now < expires_at:
            return cached_result

    def fetch():
        # 1. 嘗試 TWSE (.TW)
        tw_ticker = yf.Ticker(f"{symbol}.TW")
        try:
            info = tw_ticker.info
            if "shortName" in info or "longName" in info:
                name = info.get("shortName") or info.get("longName") or symbol
                return {
                    "formatted_symbol": f"{symbol}.TW",
                    "name": name,
                    "exchange": "TWSE",
                }
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            logger.debug(
                "TWSE symbol info fetch failed for %s.TW", symbol, exc_info=True
            )

        # 2. 嘗試 TPEx (.TWO)
        two_ticker = yf.Ticker(f"{symbol}.TWO")
        try:
            info = two_ticker.info
            if "shortName" in info or "longName" in info:
                name = info.get("shortName") or info.get("longName") or symbol
                return {
                    "formatted_symbol": f"{symbol}.TWO",
                    "name": name,
                    "exchange": "TPEx",
                }
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            logger.debug(
                "TPEx symbol info fetch failed for %s.TWO", symbol, exc_info=True
            )

        return {
            "formatted_symbol": f"{symbol}.TW",
            "name": symbol,
            "exchange": "TWSE",
        }  # Fallback

    result = await asyncio.to_thread(fetch)
    is_fallback = result["name"] == symbol  # fallback returns symbol as name
    ttl = 300 if is_fallback else 3600
    _STOCK_INFO_CACHE[symbol] = (result, now + ttl)
    return result


@router.get("/market")
async def get_tw_market(symbols: Optional[str] = None):
    """
    Get current market data for a list of TW Stock symbols.
    If no symbols are provided, returns data for the top preset symbols.

    整批回應快取 60s：上游 yfinance 假日/離峰常 >9s，auto-refresh 每
    refreshSeconds 打一次會把前端 15s timeout 撐爆（2026-08-22 盤查：
    139 連發逾時）。價格列表 60s 陳舊度可接受（UI 本來就是輪詢刷新）。
    """
    cache_key = f"market_response:{symbols or 'default'}"
    cached = _twse_cache.get(cache_key)
    if cached:
        data, expiry = cached
        now = datetime.now(timezone.utc)
        if now < expiry:
            return data
        # 過期 10 分鐘內先回舊報價、背景更新（見 _ttl_cache.swr）；更舊就同步抓
        if now - expiry < timedelta(seconds=_MARKET_MAX_STALE_SECONDS):

            async def _refresh():
                _twse_cache[cache_key] = (
                    await _compute_tw_market(symbols),
                    datetime.now(timezone.utc) + timedelta(seconds=60),
                )

            schedule_refresh((id(_twse_cache), cache_key), _refresh)
            return data

    try:
        response = await _compute_tw_market(symbols)
        _twse_cache[cache_key] = (response, datetime.now(timezone.utc) + timedelta(seconds=60))
        return response

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Failed to fetch TW market data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch TW market data, please try again later")


_MARKET_MAX_STALE_SECONDS = 600


async def _compute_tw_market(symbols: Optional[str]) -> dict:
    target_symbols = (
        [s.strip() for s in symbols.split(",")] if symbols else DEFAULT_TW_SYMBOLS
    )
    results = []

    # Fetch all stock infos concurrently
    infos = await asyncio.gather(*[_get_stock_info(sym) for sym in target_symbols])

    async def fetch_price(symbol, info):
        fmt = info["formatted_symbol"]
        price_data = await asyncio.to_thread(
            lambda sym=fmt: tw_stock_price.invoke({"ticker": sym})
        )
        if "error" not in price_data:
            return {
                "Symbol": symbol,
                "Name": info["name"],
                "Exchange": info["exchange"],
                "Close": price_data.get("current_price")
                if price_data.get("current_price") is not None
                else price_data.get("prev_close"),
                "price_change_24h": price_data.get("change_pct", 0),
                "Volume": price_data.get("recent_ohlcv", [-1])[0].get("volume", 0)
                if price_data.get("recent_ohlcv")
                else 0,
            }
        return None

    price_results = await asyncio.gather(
        *[fetch_price(sym, inf) for sym, inf in zip(target_symbols, infos)]
    )
    results = [r for r in price_results if r is not None]

    response = {
        "top_performers": results,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    return response


@limiter.limit("20/minute")



@router.get("/pulse/{symbol}")
async def get_tw_pulse(
    request: Request,
    symbol: str,
    deep_analysis: bool = False,
    force_refresh: bool = False,
    lang: str = "zh-TW",
    x_user_llm_provider: Optional[str] = Header(None),
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """
    Get detailed pulse data (Technical, Fundamental, Institutional, News) for a single TW Stock.
    """
    try:
        info = await _get_stock_info(symbol)
        formatted_symbol = info["formatted_symbol"]
        company_name = info["name"]

        price = await asyncio.to_thread(
            lambda: tw_stock_price.invoke({"ticker": formatted_symbol})
        )

        # Check for invalid symbols gracefully
        if "error" in price or price.get("current_price") is None:
            raise HTTPException(
                status_code=404,
                detail=f"TW stock symbol \"{symbol}\" not found or its real-time data is currently unavailable.",
            )

        code = symbol.split(".")[0]
        tech, funds, inst, news, rev_list, div_list, yf_extras = await asyncio.gather(
            asyncio.to_thread(
                lambda: tw_technical_analysis.invoke({"ticker": formatted_symbol})
            ),
            asyncio.to_thread(
                lambda: tw_fundamentals.invoke({"ticker": formatted_symbol})
            ),
            asyncio.to_thread(
                lambda: tw_institutional.invoke({"ticker": formatted_symbol})
            ),
            asyncio.to_thread(
                lambda: tw_news.invoke(
                    {"ticker": formatted_symbol, "company_name": company_name}
                )
            ),
            asyncio.to_thread(lambda: tw_monthly_revenue.invoke({"code": code})),
            asyncio.to_thread(lambda: tw_dividend_info.invoke({"code": code})),
            _fetch_yf_extras(formatted_symbol),
        )
        # Merge yfinance supplementary fields into funds (without overwriting existing keys)
        for k, v in yf_extras.items():
            if k not in funds:
                funds[k] = v

        # Generate a dynamic AI-like summary based on the data
        curr_price = price.get("current_price", 0)
        change = price.get("change_pct", 0)
        rsi = tech.get("rsi_14")
        foreign = inst.get("foreign_net")

        trend_str = (
            "呈現上漲趨勢"
            if change > 0
            else ("呈現下跌態勢" if change < 0 else "走勢平穩")
        )
        rsi_str = ""
        if isinstance(rsi, (int, float)):
            if rsi > 70:
                rsi_str = "，RSI 指標顯示目前可能處於超買區間，短期需留意回檔風險"
            elif rsi < 30:
                rsi_str = "，RSI 指標顯示目前可能處於超賣區間，或有反彈契機"
            else:
                rsi_str = "，RSI 指標落在中性區間"

        foreign_str = ""
        if foreign and str(foreign) not in ["", "N/A"]:
            try:
                f_val = int(str(foreign).replace(",", ""))
                if f_val > 0:
                    foreign_str = f"；籌碼方面，外資近期呈現買超 ({f_val:,} 股)"
                elif f_val < 0:
                    foreign_str = f"；籌碼方面，外資近期呈現賣超 ({f_val:,} 股)"
            except ValueError:
                logger.debug(f"Failed to parse foreign value: {foreign}")

        _dynamic_summary = f"根據最新市場數據，{company_name} ({symbol}) 目前股價為 ${curr_price}，24小時{trend_str} ({change}%){rsi_str}{foreign_str}。"

        # Only pass valid news items with links
        valid_news = (
            [{"title": n.get("title", ""), "url": n.get("url", "")} for n in news[:3]]
            if isinstance(news, list) and len(news) > 0 and "error" not in news[0]
            else []
        )

        # Filter out N/A foreign_net from key_points
        key_points = [
            f"RSI(14): {tech.get('rsi_14', 'N/A')}",
            f"MACD: {tech.get('macd', {}).get('histogram', 'N/A')}",
            f"P/E Ratio: {funds.get('pe_ratio', 'N/A')}",
        ]

        foreign_net = inst.get("foreign_net", "N/A")
        if foreign_net and str(foreign_net) != "N/A":
            key_points.append(f"Foreign Net Buy/Sell: {foreign_net} shares")

        # 不顯示 fallback summary — 只有 deep_analysis 才有內容
        final_summary = None
        ai_error = None
        source_mode = "on_demand"
        credentials = None
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
            if isinstance(news, list) and len(news) > 0 and "error" not in news[0]:
                headlines = "\n".join(
                    f"- {n.get('title', '')}" for n in news[:3] if n.get("title")
                )
                if headlines:
                    news_str = f"\n近期新聞:\n{headlines}"

            ma_dict = tech.get("ma", {})
            tp = funds.get("target_price")
            tp_str = (
                str(tp)
                if not tp
                else (
                    f"{tp} ({(tp - curr_price) / curr_price * 100:+.1f}%)"
                    if curr_price
                    else str(tp)
                )
            )
            rg = funds.get("revenue_growth")
            eg = funds.get("earnings_growth")
            context = (
                f"台股: {company_name} ({symbol})\n"
                f"現價: ${curr_price}\n"
                f"24H 漲跌幅: {change}%\n"
                f"P/E Ratio: {funds.get('pe_ratio', 'N/A')}　PB: {funds.get('pb_ratio', 'N/A')}\n"
                f"EPS(TTM): {funds.get('eps_ttm', 'N/A')}　股息殖利率: {funds.get('dividend_yield_pct', 'N/A')}%\n"
                f"營收成長: {f'{rg:+.1f}%' if rg is not None else 'N/A'}　獲利成長: {f'{eg:+.1f}%' if eg is not None else 'N/A'}\n"
                f"Beta(β): {funds.get('beta', 'N/A')}\n"
                f"第三方分析師預估目標價（非本平台預測）: {tp_str}　分析師建議: {(funds.get('recommendation') or 'N/A').upper()}\n"
                f"所屬產業: {funds.get('sector', 'N/A')} / {funds.get('industry', 'N/A')}\n"
                f"外資買賣超: {inst.get('foreign_net', 'N/A')} 股\n"
                f"RSI(14): {tech.get('rsi_14', 'N/A')}\n"
                f"MACD Histogram: {tech.get('macd', {}).get('histogram', 'N/A') if isinstance(tech.get('macd'), dict) else 'N/A'}\n"
                f"MA20: {ma_dict.get('ma20', 'N/A')}\n"
                f"MA60: {ma_dict.get('ma60', 'N/A')}\n"
                f"52W High: {funds.get('52w_high', 'N/A')}\n"
                f"52W Low: {funds.get('52w_low', 'N/A')}"
                f"{news_str}"
            )
            ai_result = await get_deep_analysis(
                market="twstock",
                symbol=symbol,
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
                final_summary = ai_result["report"].get("summary")

        # Format structure to match Market Pulse
        return {
            "symbol": symbol,
            "company_name": company_name,
            "current_price": curr_price,
            "change_24h": change,
            "status": "completed",
            "source_mode": source_mode,
            "ai_error": ai_error,
            "report": {
                "summary": final_summary,
                "key_points": key_points,
                "highlights": valid_news,
                "risks": [],
            },
            "technical_indicators": tech,
            "fundamentals": funds,
            "institutional": inst,
            "news": news,
            "monthly_revenue": rev_list[0]
            if isinstance(rev_list, list) and rev_list and "error" not in rev_list[0]
            else None,
            "dividend_info": div_list[0]
            if isinstance(div_list, list) and div_list and "error" not in div_list[0]
            else None,
            "cached_at": cached_at,
            "cache_expires_at": cache_expires_at,
            "cooldown_remaining": cooldown_remaining,
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Failed to fetch TW pulse data for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch TW pulse data, please try again later")


@limiter.limit("30/minute")



@router.get("/klines/{symbol}")
async def get_tw_klines(request: Request, symbol: str, interval: str = "1d", limit: int = 100):
    """
    Get historical K-line (OHLCV) data for TW Stock to render charts.
    interval: '1d', '1wk', '1mo'
    """
    try:
        info = await _get_stock_info(symbol)
        formatted_symbol = info["formatted_symbol"]

        # Calculate period based on interval and limit
        # For TW stocks via yfinance, 1d is usually fine for a few months/years
        period = "1y"
        if interval == "1wk":
            period = "2y"
        elif interval == "1mo":
            period = "5y"

        hist = await asyncio.to_thread(
            lambda: yf.Ticker(formatted_symbol).history(
                period=period, interval=interval
            )
        )

        if hist.empty:
            raise HTTPException(status_code=404, detail="No trading data available, or the stock has been delisted")

        # Format for Lightweight Charts: { time, open, high, low, close, volume }
        klines = []
        for index, row in hist.iterrows():
            # index is a pandas Timestamp
            # Lightweight Charts expects 'time' as a string 'YYYY-MM-DD' for daily chart
            # Or unix timestamp
            try:
                time_val = index.strftime("%Y-%m-%d")
                klines.append(
                    {
                        "time": time_val,
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

        # Keep only the requested limit
        klines = klines[-limit:]

        return {"symbol": symbol, "interval": interval, "data": klines}

    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Failed to fetch TW klines data for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch TW K-line data, please try again later")


# ── TWSE OpenAPI 代理端點 ─────────────────────────────────────────────────────

TWSE_BASE = "https://openapi.twse.com.tw/v1"


@router.get("/opendata/news")
async def get_tw_major_news(
    limit: int = 15,
    symbols: str = Query(
        None, description="Comma-separated stock codes to filter (e.g., '2330,2317')"
    ),
):
    """
    取得上市公司每日重大訊息（來源：TWSE t187ap04_L）。
    """
    try:
        data = await _fetch_twse(
            f"{TWSE_BASE}/opendata/t187ap04_L", cache_key="twse_news"
        )

        if symbols:
            target_symbols = [s.strip() for s in symbols.split(",")]
            data = [d for d in (data or []) if d.get("公司代號") in target_symbols]

        results = []
        for item in (data or [])[:limit]:
            results.append(
                {
                    "date": item.get("發言日期", ""),
                    "time": item.get("發言時間", ""),
                    "code": item.get("公司代號", ""),
                    "name": item.get("公司名稱", ""),
                    "subject": item.get("主旨 ", "").strip()
                    or item.get("主旨", "").strip(),
                    "rule": item.get("符合條款", ""),
                    "fact_date": item.get("事實發生日", ""),
                    "description": item.get("說明", ""),
                }
            )
        return {
            "data": results,
            "total": len(results),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[TW News] {e}", exc_info=True)
        raise HTTPException(
            status_code=502, detail="TWSE announcements API call failed, please try again later"
        )


def _pe_row(d: dict, symbol: str) -> dict:
    return {
        "code": d.get("Code", symbol),
        "name": d.get("Name", ""),
        "date": d.get("Date", ""),
        "pe_ratio": d.get("PEratio", "N/A"),
        "dividend_yield": d.get("DividendYield", "N/A"),
        "pb_ratio": d.get("PBratio", "N/A"),
        "dividend_year": d.get("DividendYear", ""),
        "fiscal_quarter": d.get("FiscalYearQuarter", ""),
    }


async def _pe_rows_by_code(symbols: list[str]) -> dict[str, dict]:
    """BWIBBU_d 找不到的代號再查 BWIBBU_ALL（兩份都有快取，整批只各抓一次）。"""
    data = await _fetch_twse(f"{TWSE_BASE}/exchangeReport/BWIBBU_d", cache_key="twse_pe_all")
    wanted = set(symbols)
    found: dict[str, dict] = {}
    for d in data or []:  # 同代號多筆時取第一筆（跟原本單檔端點的 matching[0] 一致）
        code = d.get("Code", "")
        if code in wanted:
            found.setdefault(code, d)
    missing = wanted - found.keys()
    if missing:
        data2 = await _fetch_twse(
            f"{TWSE_BASE}/exchangeReport/BWIBBU_ALL", cache_key="twse_pe_all2"
        )
        for d in data2 or []:
            code = d.get("Code", "")
            if code in missing and code not in found:
                found[code] = d
    return found


_PE_BATCH_MAX = 20
_PE_SYMBOL_RE = re.compile(r"^[0-9A-Za-z]{1,10}$")


@router.get("/opendata/pe_ratio")
async def get_tw_pe_ratio_batch(symbols: str = ""):
    """
    一次取多檔的本益比、殖利率、股價淨值比（逗號分隔，最多 20 檔）。
    台股頁以前每檔各打一次 /opendata/pe_ratio/{symbol}（2026-09-26 量測：一次 10 個請求）；
    每筆的欄位與單檔端點相同，找不到的代號直接略過。
    """
    codes = [s.strip() for s in symbols.split(",") if s.strip()]
    codes = list(dict.fromkeys(codes))[:_PE_BATCH_MAX]
    if not codes or not all(_PE_SYMBOL_RE.match(c) for c in codes):
        raise HTTPException(
            status_code=400, detail="symbols must be 1-20 comma-separated stock codes"
        )
    try:
        found = await _pe_rows_by_code(codes)
        return {"data": [_pe_row(found[c], c) for c in codes if c in found]}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[TW PE Ratio batch] {e}", exc_info=True)
        raise HTTPException(
            status_code=502, detail="TWSE P/E ratio API call failed, please try again later"
        )


@router.get("/opendata/pe_ratio/{symbol}")
async def get_tw_pe_ratio(symbol: str):
    """
    取得個股本益比、殖利率、股價淨值比（來源：TWSE BWIBBU_d）。
    symbol: 股票代號，如 2330
    """
    try:
        found = await _pe_rows_by_code([symbol])
        if symbol not in found:
            raise HTTPException(
                status_code=404, detail=f"No P/E ratio data found for stock symbol {symbol}"
            )
        return _pe_row(found[symbol], symbol)
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[TW PE Ratio] {e}", exc_info=True)
        raise HTTPException(
            status_code=502, detail="TWSE P/E ratio API call failed, please try again later"
        )


@router.get("/opendata/monthly_revenue")
async def get_tw_monthly_revenue(limit: int = 50):
    """
    取得上市公司每月營業收入彙總（來源：TWSE t187ap05_L）。
    """
    try:
        data = await _fetch_twse(
            f"{TWSE_BASE}/opendata/t187ap05_L", cache_key="twse_monthly_rev"
        )
        results = []
        for item in (data or [])[:limit]:
            results.append(
                {
                    "code": item.get("公司代號", ""),
                    "name": item.get("公司名稱", ""),
                    "industry": item.get("產業別", ""),
                    "ym": item.get("資料年月", ""),
                    "current_revenue": item.get("營業收入-當月營收", ""),
                    "mom_change_pct": item.get("營業收入-上月比較增減(%)", ""),
                    "yoy_change_pct": item.get("營業收入-去年當月增減(%)", ""),
                    "ytd_revenue": item.get("累計營業收入-當月累計營收", ""),
                    "ytd_yoy_pct": item.get("累計營業收入-前期比較增減(%)", ""),
                }
            )
        return {
            "data": results,
            "total": len(results),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[TW Monthly Revenue] {e}", exc_info=True)
        raise HTTPException(
            status_code=502, detail="TWSE monthly revenue API call failed, please try again later"
        )


@router.get("/opendata/dividend")
async def get_tw_dividend(
    limit: int = 50,
    symbols: str = Query(None, description="Comma-separated stock codes to filter"),
):
    """
    取得上市公司股利分派情形（來源：TWSE t187ap45_L）。
    """
    try:
        data = await _fetch_twse(
            f"{TWSE_BASE}/opendata/t187ap45_L", cache_key="twse_dividend"
        )

        if symbols:
            target_symbols = [s.strip() for s in symbols.split(",")]
            data = [d for d in (data or []) if d.get("公司代號") in target_symbols]

        results = []
        for item in (data or [])[:limit]:
            results.append(
                {
                    "code": item.get("公司代號", ""),
                    "name": item.get("公司名稱", ""),
                    "year": item.get("股利年度", ""),
                    "progress": item.get("決議（擬議）進度", ""),
                    "board_date": item.get("董事會（擬議）股利分派日", ""),
                    "shareholder_meeting": item.get("股東會日期", ""),
                    "cash_dividend": item.get("股東配發-盈餘分配之現金股利(元/股)", ""),
                    "stock_dividend": item.get("股東配發-盈餘轉增資配股(元/股)", ""),
                    "net_profit": item.get("本期淨利(淨損)(元)", ""),
                }
            )
        return {
            "data": results,
            "total": len(results),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[TW Dividend] {e}", exc_info=True)
        raise HTTPException(
            status_code=502, detail="TWSE dividend API call failed, please try again later"
        )


@router.get("/opendata/foreign_holding")
async def get_tw_foreign_holding():
    """
    取得集中市場外資及陸資持股前 20 名（來源：TWSE MI_QFIIS_sort_20）。
    """
    try:
        data = await _fetch_twse(
            f"{TWSE_BASE}/fund/MI_QFIIS_sort_20", cache_key="twse_foreign_top20"
        )
        results = []
        for item in data or []:
            results.append(
                {
                    "rank": item.get("Rank", ""),
                    "code": item.get("Code", ""),
                    "name": item.get("Name", ""),
                    "total_shares": item.get("ShareNumber", ""),
                    "available_shares": item.get("AvailableShare", ""),
                    "held_shares": item.get("SharesHeld", ""),
                    "available_pct": item.get("AvailableInvestPer", ""),
                    "held_pct": item.get("SharesHeldPer", ""),
                    "upper_limit_pct": item.get("Upperlimit", ""),
                }
            )
        return {
            "data": results,
            "total": len(results),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[TW Foreign Holding] {e}", exc_info=True)
        raise HTTPException(
            status_code=502, detail="TWSE foreign holding API call failed, please try again later"
        )
