"""
yf_helpers.py — 共用 yfinance 資料抓取工具
替代各市場路由中直接呼叫 Yahoo Finance HTTP API 的做法，
改用 yfinance library（內建 cookie/crumb 認證），避免在生產伺服器被擋。

使用方式：
    from api.routers.yf_helpers import fetch_quote_sync, fetch_technicals_sync, fetch_klines_sync
"""

from __future__ import annotations

import asyncio
from typing import Optional

from api.utils import logger
from core.providers.yahoo_provider import rsi_wilder
from utils.lazy_import import lazy_module

yf = lazy_module("yfinance")  # 第一次用到才載入（見 utils/lazy_import.py）

# ── 單檔報價 ──────────────────────────────────────────────────────────────────


def quote_extras(fast_info, decimal_places: int = 2) -> dict:
    """fast_info 順手就有的成交量、當日區間、52 週區間（不另外打請求）。

    拿不到或不合理（指數／外匯的成交量是 0、區間顛倒）的欄位回 None，前端整列略過。
    """

    def read(attr: str):
        try:
            value = getattr(fast_info, attr)
            if value is None or value != value:  # None／NaN
                return None
            return float(value)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return None

    volume = read("last_volume")
    day_high, day_low = read("day_high"), read("day_low")
    year_high, year_low = read("year_high"), read("year_low")
    if day_high is not None and day_low is not None and day_high < day_low:
        day_high = day_low = None
    if year_high is not None and year_low is not None and year_high < year_low:
        year_high = year_low = None
    # 一年內高低差 10 倍以上多半是拆股／併股沒調整的假象（2026-10-05 實測 TOPIX ETF 1306.T：
    # 一年低點 36.7、現價 435）——寧可不顯示 52 週區間，也不要畫出一條錯的
    if year_high and year_low and year_high / year_low > 10:
        year_high = year_low = None

    def rounded(value):
        return None if value is None else round(value, decimal_places)

    return {
        "volume": int(volume) if volume and volume > 0 else None,
        "dayHigh": rounded(day_high),
        "dayLow": rounded(day_low),
        "yearHigh": rounded(year_high),
        "yearLow": rounded(year_low),
    }


def fetch_quote_sync(
    symbol: str,
    names_dict: dict[str, dict],
    decimal_places: int = 2,
    default_currency: str = "USD",
) -> Optional[dict]:
    """
    用 yfinance fast_info 抓取單一股票報價（同步）。
    呼叫方式：await asyncio.to_thread(fetch_quote_sync, symbol, names_dict, ...)

    Returns dict with keys: symbol, name, name_zh, name_en, price, change,
                            changePercent, currency
    Returns None on failure.
    """
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info

        price = fi.last_price
        prev = fi.previous_close

        if price is None or price != price:  # NaN check — fallback to ticker.info
            try:
                info = ticker.info
                price = info.get("regularMarketPrice") or info.get("currentPrice")
                if price is None or price != price:
                    return None
                if prev is None:
                    prev = info.get("regularMarketPreviousClose") or info.get(
                        "previousClose"
                    )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                return None

        # Some indices (e.g. ^TPX, ^KQ11) have fast_info.previous_close=None
        # Fallback to history-based prev close — more reliable for indices
        if prev is None:
            try:
                prev = ticker.info.get("regularMarketPreviousClose")
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                prev = None

        # Last resort: use history to derive price and prev_close for indices
        if (price is None or price != price) or (prev is None):
            try:
                hist = ticker.history(period="5d", interval="1d")
                if not hist.empty:
                    closes = hist["Close"].dropna().tolist()
                    if closes:
                        if price is None or price != price:
                            price = closes[-1]
                        if prev is None and len(closes) >= 2:
                            prev = closes[-2]
                        elif prev is None:
                            prev = closes[-1]
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass

        if price is None or price != price:
            return None

        price = round(float(price), decimal_places)
        prev = round(float(prev), decimal_places) if prev else price
        change = round(price - prev, decimal_places)
        change_pct = round((change / prev) * 100, 2) if prev else 0.0

        # Try currency from fast_info, fallback to default
        try:
            currency = fi.currency or default_currency
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            currency = default_currency

        names = names_dict.get(symbol)
        if names:
            name_zh = names["zh"]
            name_en = names["en"]
        else:
            # Fallback: try to read shortName from ticker.info (slow, only for unknowns)
            try:
                info = ticker.info
                fallback = info.get("shortName") or info.get("longName") or symbol
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                fallback = symbol
            name_zh = fallback
            name_en = fallback

        return {
            "symbol": symbol,
            "name": name_zh,  # backward compat
            "name_zh": name_zh,
            "name_en": name_en,
            "price": price,
            "change": change,
            "changePercent": change_pct,
            "currency": currency,
            **quote_extras(fi, decimal_places),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[yf_helpers] quote failed {symbol}: {e}")
        return None


async def fetch_quotes(
    symbols: list[str],
    names_dict: dict[str, dict],
    decimal_places: int = 2,
    default_currency: str = "USD",
) -> list[dict]:
    """非同步並行抓取多檔報價。"""
    results = await asyncio.gather(
        *[
            asyncio.to_thread(
                fetch_quote_sync, s, names_dict, decimal_places, default_currency
            )
            for s in symbols
        ]
    )
    return [r for r in results if r]


# ── 技術指標（RSI + MACD + MA20/50 + 52W）───────────────────────────────────


def fetch_technicals_sync(symbol: str, decimal_places: int = 2) -> dict:
    """
    用 yfinance history 計算 RSI(14)、MACD histogram、MA20/50 與 52 週高低點（同步）。
    """
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1y", interval="1d")
        if hist.empty or len(hist) < 15:
            return {}

        closes_series = hist["Close"].dropna()
        closes = closes_series.tolist()
        if len(closes) < 15:
            return {}

        # RSI(14) — Wilder 平滑，與 AI 工具同一套（見 rsi_wilder）
        rsi = rsi_wilder(closes_series)

        # MACD (EMA12 - EMA26，signal=EMA9)
        ema12 = closes_series.ewm(span=12).mean()
        ema26 = closes_series.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_histogram = round(
            float(macd_line.iloc[-1] - signal_line.iloc[-1]), decimal_places
        )

        # MA20 / MA50
        ma20 = (
            round(float(closes_series.rolling(20).mean().iloc[-1]), decimal_places)
            if len(closes) >= 20
            else None
        )
        ma50 = (
            round(float(closes_series.rolling(50).mean().iloc[-1]), decimal_places)
            if len(closes) >= 50
            else None
        )

        highs = [h for h in hist["High"].tolist() if h is not None and h == h]
        lows = [lo for lo in hist["Low"].tolist() if lo is not None and lo == lo]
        high_52w = round(max(highs), decimal_places) if highs else None
        low_52w = round(min(lows), decimal_places) if lows else None

        return {
            "rsi": rsi,
            "macd_histogram": macd_histogram,
            "ma20": ma20,
            "ma50": ma50,
            "52w_high": high_52w,
            "52w_low": low_52w,
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[yf_helpers] technicals failed {symbol}: {e}")
        return {}


def technicals_for_display(tech: dict) -> dict:
    """要點／LLM prompt 用：算不出來的指標（None）顯示成 N/A。

    ``fetch_technicals_sync`` 資料不足時 rsi／ma20／ma50 等會是 None，
    ``tech.get("rsi", "N/A")`` 擋不到，prompt 裡就印出「RSI(14): None」。
    原始 dict 照舊回給前端（technical_indicators 要保留 null）。
    """
    return {k: ("N/A" if v is None else v) for k, v in (tech or {}).items()}


# ── K 線 ─────────────────────────────────────────────────────────────────────


def fetch_klines_sync(
    symbol: str,
    interval: str = "1d",
    limit: int = 200,
    decimal_places: int = 2,
) -> list[dict]:
    """
    用 yfinance history 取得 OHLCV K 線資料（同步）。
    """
    period_map = {"1d": "1y", "1wk": "2y", "1mo": "5y"}
    period = period_map.get(interval, "1y")

    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period, interval=interval)
    if hist.empty:
        raise ValueError("No trading data available or the stock has been delisted")

    klines = []
    for idx, row in hist.iterrows():
        try:
            klines.append(
                {
                    "time": idx.strftime("%Y-%m-%d"),
                    "open": round(float(row["Open"]), decimal_places),
                    "high": round(float(row["High"]), decimal_places),
                    "low": round(float(row["Low"]), decimal_places),
                    "close": round(float(row["Close"]), decimal_places),
                    "volume": int(row["Volume"]),
                }
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            continue

    return klines[-limit:]


# ── 新聞 ──────────────────────────────────────────────────────────────────────


def fetch_news_sync(symbol: str, limit: int = 5) -> list[dict]:
    """用 yfinance 抓新聞（同步）。

    透過共用 YahooFinanceProvider.get_news，享 300s L1+L2 快取（業界 news feeds
    TTL 慣例 1-15min），避免每次呼叫都打 yfinance。適用所有 yfinance 支援的市場
    （JP/KR/IN/HK/A/Forex/Commodity）。
    """
    try:
        return _shared_yf_provider().get_news(symbol, limit)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[yf_helpers] news failed {symbol}: {e}")
        return []


async def fetch_news(
    symbols: list[str],
    limit_per: int = 5,
    market: Optional[str] = None,
    names: Optional[dict] = None,
    lang: str = "zh-TW",
) -> list[dict]:
    """並行抓取多個 symbol 的新聞，去重後依時間降冪排序。

    給了 ``market``（jp／kr／cn／hk／commodity／forex）就再併入免費來源（Google News、
    日股 TDnet 公告、A 股東方財富公告，見 news_feeds.py）——Yahoo 對這些市場幾乎沒有新聞。
    ``names`` 是該市場的名稱表，用來組搜尋詞。
    """
    jobs = [asyncio.to_thread(fetch_news_sync, s, limit_per) for s in symbols]
    if market:
        from api.routers.news_feeds import fetch_feed_news

        jobs += [fetch_feed_news(market, s, names, lang, limit_per) for s in symbols]
    results = await asyncio.gather(*jobs)
    seen, merged = set(), []
    for items in results:
        for item in items:
            if item["title"] not in seen:
                seen.add(item["title"])
                merged.append(item)
    merged.sort(key=lambda x: x.get("published", 0), reverse=True)
    return merged


# ── 基本面 extras（PE / Beta / 股息 / EPS / 成長率 / 分析師目標等）────────────
# 已重構為 thin wrapper：實際邏輯統一在 core.providers.YahooFinanceProvider，
# 避免兩處邏輯飄移。保留此 helper 是為了向後相容。
def fetch_extras_sync(symbol: str) -> dict:
    """
    用 yfinance ticker.info 抓取補充基本面數據（同步）。
    適用所有 yfinance 支援的市場（JP/KR/HK/IN/A/TW/US 等）。

    呼叫者通常會傳入已含後綴的 symbol（如 '2330.TW'、'0700.HK'），
    Provider 的 normalize 偵測到後綴後不會再加工。

    回傳欄位（缺失時略過）：
      pe_ratio, pb_ratio, beta, dividend_yield, eps,
      revenue_growth, earnings_growth, profit_margins,
      target_price, recommendation, sector, industry,
      avg_volume, market_cap, volume
    """

    # market="us" 是中性預設；當 symbol 已含後綴時 normalize 會保持原樣
    return _shared_yf_provider().get_fundamentals(symbol)


def _shared_yf_provider():
    """Lazy-init 一個共用的 YahooFinanceProvider 實例，給 helper 函數使用。"""
    global _SHARED_YF_PROVIDER
    if _SHARED_YF_PROVIDER is None:
        from core.providers.yahoo_provider import YahooFinanceProvider

        _SHARED_YF_PROVIDER = YahooFinanceProvider(market="us")
    return _SHARED_YF_PROVIDER


_SHARED_YF_PROVIDER = None


async def fetch_extras(symbol: str) -> dict:
    """非同步包裝 fetch_extras_sync，供路由層 asyncio.gather 使用。"""
    return await asyncio.to_thread(fetch_extras_sync, symbol)
