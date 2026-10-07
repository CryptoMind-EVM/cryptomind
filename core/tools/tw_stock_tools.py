"""
Taiwan Stock Tools — 5 @tool functions for TWStockAgent.

Data sources (free-first):
  - yfinance: price + OHLCV + basic fundamentals (15min delay)
  - TWSE openapi: institutional (3-party) data
  - Google News RSS: TW-specific news
  - FinMind: richer fundamentals (free tier, rate-limited)
"""

import asyncio

from cachetools import TTLCache
from langchain_core.tools import tool

from core.tools.helpers import crypto_misroute_error, is_crypto_symbol


def _normalize_tw_ticker(ticker: str) -> str:
    """
    將各種台股代碼格式標準化為 yfinance 格式。

    支援的輸入格式：
    - 純數字：'[代號]' -> '[代號].TW'
    - 已有後綴：'[代號].TW' -> '[代號].TW'
    - 上櫃股票：'[代號].TWO' -> '[代號].TWO'
    - 中文名稱：使用 TWSymbolResolver 解析

    這是一個通用的邊界處理，不是針對特定案例的 hardcode。
    """
    if not ticker:
        return ticker

    s = ticker.strip()
    upper = s.upper()

    # Rule 1: 已經是正確格式
    if upper.endswith(".TW") or upper.endswith(".TWO"):
        return upper

    # Rule 2: 純數字代碼（4-6位）-> 自動添加 .TW 後綴
    if s.isdigit() and 4 <= len(s) <= 6:
        return f"{s}.TW"

    # Rule 3: 非數字，嘗試使用 resolver 解析中文名稱
    if not s.isdigit():
        try:
            from .tw_symbol_resolver import TWSymbolResolver

            resolver = TWSymbolResolver()
            resolved = resolver.resolve(s)
            if resolved:
                return resolved
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass

    # Rule 4: 無法解析，返回原值（讓 yfinance 報錯）
    return s


# ── Price ──────────────────────────────────────────────────────────────────


def _tw():
    """取得統一 TWSEProvider（lazy singleton）。"""
    from core.providers import get_provider

    return get_provider("tw")


@tool
def tw_stock_price(ticker: str) -> dict:
    """獲取台股即時（15分鐘延遲）及近期 OHLCV 價格資料。
    支援各種格式：純代號、含市場後綴代號、公司名稱"""
    if is_crypto_symbol(ticker):
        return crypto_misroute_error(ticker, "TW stock")
    try:
        ticker = _normalize_tw_ticker(ticker)
        provider = _tw()
        price = provider.get_price(ticker)
        klines = provider.get_klines(ticker, interval="1d", limit=5)

        recent_ohlcv = [
            {
                "date": k.get("time"),
                "open": k.get("open"),
                "high": k.get("high"),
                "low": k.get("low"),
                "close": k.get("close"),
                "volume": k.get("volume"),
            }
            for k in klines
        ]

        return {
            "ticker": ticker,
            "current_price": price.get("price"),
            "prev_close": price.get("prev_close"),
            "change_pct": price.get("changePercent"),
            "recent_ohlcv": recent_ohlcv,
            "note": "Prices are real-time (about 15-minute delay)",
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── Technical Analysis ──────────────────────────────────────────────────────


@tool
def tw_technical_analysis(ticker: str, period: str = "3mo") -> dict:
    """計算台股技術指標：RSI(14)、MACD、KD(9,3,3)、MA5/20/60。
    支援各種格式：純代號、含市場後綴代號、公司名稱"""
    if is_crypto_symbol(ticker):
        return crypto_misroute_error(ticker, "TW stock")
    try:
        import yfinance as yf

        ticker = _normalize_tw_ticker(ticker)
        import pandas_ta as ta

        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty or len(hist) < 30:
            return {"ticker": ticker, "error": "Insufficient historical data (at least 30 days required)"}

        df = hist.copy()

        # RSI(14)
        rsi = ta.rsi(df["Close"], length=14)
        rsi_val = (
            round(float(rsi.iloc[-1]), 2) if rsi is not None and not rsi.empty else None
        )

        # MACD(12,26,9)——pandas_ta 欄位順序是 MACD, MACDh(histogram), MACDs(signal)，用欄名取
        macd_df = ta.macd(df["Close"])
        macd_val = macd_sig = macd_hist_val = None
        if macd_df is not None and not macd_df.empty:
            last = macd_df.iloc[-1]
            macd_val = round(float(last["MACD_12_26_9"]), 4)
            macd_sig = round(float(last["MACDs_12_26_9"]), 4)
            macd_hist_val = round(float(last["MACDh_12_26_9"]), 4)

        # KD (Stochastic 9,3,3)
        stoch = ta.stoch(df["High"], df["Low"], df["Close"], k=9, d=3, smooth_k=3)
        k_val = d_val = None
        if stoch is not None and not stoch.empty:
            k_val = round(float(stoch.iloc[-1, 0]), 2)
            d_val = round(float(stoch.iloc[-1, 1]), 2)

        # Moving Averages
        def ma(n):
            s = df["Close"].rolling(n).mean()
            return (
                round(float(s.iloc[-1]), 2)
                if not s.empty and not s.isna().iloc[-1]
                else None
            )

        close_now = round(float(df["Close"].iloc[-1]), 2)

        return {
            "ticker": ticker,
            "period": period,
            "close": close_now,
            "rsi_14": rsi_val,
            "macd": {"macd": macd_val, "signal": macd_sig, "histogram": macd_hist_val},
            "kd": {"k": k_val, "d": d_val},
            "ma": {"ma5": ma(5), "ma20": ma(20), "ma60": ma(60)},
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── Fundamentals ────────────────────────────────────────────────────────────


@tool
def tw_fundamentals(ticker: str) -> dict:
    """獲取台股基本面資料：本益比(P/E)、股價淨值比(P/B)、殖利率、EPS 等。
    透過 TWSEProvider 取得（TWSE 官方 PE 優先 + yfinance 補齊其他欄位）。
    """
    if is_crypto_symbol(ticker):
        return crypto_misroute_error(ticker, "TW stock")
    try:
        ticker = _normalize_tw_ticker(ticker)
        provider = _tw()
        fund = provider.get_fundamentals(ticker)
        tech = provider.get_technicals(ticker)
        price = provider.get_price(ticker)

        return {
            "ticker": ticker,
            "company_name": price.get("name"),
            "pe_ratio": fund.get("pe_ratio"),
            "pb_ratio": fund.get("pb_ratio"),
            "dividend_yield_pct": fund.get("dividend_yield"),
            "eps_ttm": fund.get("eps"),
            "revenue_growth": fund.get("revenue_growth"),
            "profit_margins": fund.get("profit_margins"),
            "market_cap": fund.get("market_cap"),
            "52w_high": tech.get("52w_high"),
            "52w_low": tech.get("52w_low"),
            "note": "Fundamentals: TWSE official PE/PB/yield + yfinance EPS/growth",
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── Institutional (三大法人) ────────────────────────────────────────────────


# Note: _parse_inst_number helper removed — TWSEProvider 內部已有對應邏輯


@tool
def tw_institutional(ticker: str) -> dict:
    """獲取台股三大法人籌碼資料（外資、投信、自營商買賣超）。
    資料來源：TWSE T86，透過 TWSEProvider 統一抓取。
    """
    if is_crypto_symbol(ticker):
        return crypto_misroute_error(ticker, "TW stock")
    try:
        return _tw().get_twse_institutional_detail(ticker)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── News ────────────────────────────────────────────────────────────────────

# 業界 news feeds TTL 慣例 1-15min（Imperva/Finnhub/FMP），取保守 5min。
_NEWS_CACHE: TTLCache = TTLCache(maxsize=256, ttl=300)
# 外資持股為日頻資料（業界 daily 快取數小時是常態），取 6h。
_FOREIGN_HOLDING_CACHE: TTLCache = TTLCache(maxsize=4, ttl=21600)


@tool
def tw_news(ticker: str, company_name: str = "", limit: int = 8) -> list:
    """從 Google News RSS 獲取台股相關新聞。
    company_name: 公司中文名稱，可提升新聞相關性"""
    if is_crypto_symbol(ticker):
        return [crypto_misroute_error(ticker, "TW stock")]
    cache_key = f"tw_news:{ticker}:{company_name}:{limit}"
    cached = _NEWS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        from urllib.parse import quote

        import httpx
        from defusedxml import ElementTree as ET

        # Search term: prefer Chinese company name for better results
        search_term = company_name if company_name else ticker
        query = quote(f"{search_term} 股票")
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"

        resp = httpx.get(rss_url, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.content)
        _ = {"media": "http://search.yahoo.com/mrss/"}  # namespace not used
        items = []

        for item in root.findall(".//item")[:limit]:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")
            source = item.findtext("source", "")
            items.append(
                {
                    "title": title,
                    "url": link,
                    "published": pub_date,
                    "source": source,
                }
            )

        _NEWS_CACHE[cache_key] = items
        return items
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return [{"error": str(e)}]


# ── TWSE OpenAPI Tools ────────────────────────────────────────────────────────

TWSE_BASE = "https://openapi.twse.com.tw/v1"


@tool
def tw_major_news(limit: int = 10) -> list:
    """獲取上市公司今日重大訊息（公告）清單。
    資料來源：TWSE OpenAPI t187ap04_L。
    包含公司代號、公司名稱、主旨、發言時間等。
    limit: 回傳筆數上限（預設10）"""
    cache_key = f"tw_major_news:{limit}"
    cached = _NEWS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        import httpx

        resp = httpx.get(f"{TWSE_BASE}/opendata/t187ap04_L", timeout=15)
        data = resp.json() if resp.status_code == 200 else []
        results = []
        for item in (data or [])[:limit]:
            subject = item.get("主旨 ", "").strip() or item.get("主旨", "").strip()
            results.append(
                {
                    "date": item.get("發言日期", ""),
                    "time": item.get("發言時間", ""),
                    "code": item.get("公司代號", ""),
                    "name": item.get("公司名稱", ""),
                    "subject": subject,
                    "rule": item.get("符合條款", ""),
                }
            )
        _NEWS_CACHE[cache_key] = results
        return results
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return [{"error": str(e)}]


@tool
def tw_pe_ratio(code: str) -> dict:
    """獲取台股個股本益比(P/E)、殖利率、股價淨值比(PBR)。
    資料來源：TWSE OpenAPI（透過 TWSEProvider）。
    code: 股票代號"""
    if is_crypto_symbol(code):
        return crypto_misroute_error(code, "TW stock")
    try:
        return _tw().get_twse_pe_detail(code)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"code": code, "error": str(e)}


@tool
def tw_monthly_revenue(code: str = "") -> list:
    """獲取台股上市公司月營業收入資料。
    資料來源：TWSE OpenAPI t187ap05_L（透過 TWSEProvider）。
    包含當月營收、月增率、年增率、累計營收。
    code: 股票代號，若為空字串則返回全市場前 30 筆"""
    if code and is_crypto_symbol(code):
        return crypto_misroute_error(code, "TW stock")
    try:
        return _tw().get_twse_monthly_revenue_detail(code)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return [{"error": str(e)}]


@tool
def tw_dividend_info(code: str = "") -> list:
    """獲取台股上市公司股利分派情形。
    資料來源：TWSE OpenAPI t187ap45_L（透過 TWSEProvider）。
    包含現金股利、配股、股東會日期等。
    code: 股票代號，若為空字串則返回近期所有公司前 30 筆"""
    if code and is_crypto_symbol(code):
        return crypto_misroute_error(code, "TW stock")
    try:
        return _tw().get_twse_dividend_detail(code)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return [{"error": str(e)}]


@tool
def tw_stock_snapshot(ticker: str) -> dict:
    """一次取得台股完整快照（透過統一 TWSEProvider 並行抓取）。
    包含：即時價格 + 技術指標(RSI/MACD/MA/52W) + 基本面(PE/EPS/股息率) +
          TWSE extras(三大法人/月營收/股利) + 最新新聞。
    接受股票代號（如 2330）或公司名稱（如台積電）。
    """
    from core.providers import get_provider

    if is_crypto_symbol(ticker):
        return crypto_misroute_error(ticker, "TW stock")
    try:
        normalized = _normalize_tw_ticker(ticker)
        return get_provider("tw").get_snapshot(normalized)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": str(e), "ticker": ticker}


@tool
def tw_foreign_holding_top20() -> list:
    """獲取集中市場外資及陸資持股前 20 名彙總表。
    資料來源：TWSE OpenAPI MI_QFIIS_sort_20。
    包含持股比率、尚可投資比率、法令投資上限等。
    適合用來了解外資最集中持股的台股標的。"""
    cached = _FOREIGN_HOLDING_CACHE.get("tw_foreign_holding_top20")
    if cached is not None:
        return cached
    try:
        import httpx

        resp = httpx.get(f"{TWSE_BASE}/fund/MI_QFIIS_sort_20", timeout=15)
        data = resp.json() if resp.status_code == 200 else []
        results = []
        for item in data or []:
            results.append(
                {
                    "rank": item.get("Rank", ""),
                    "code": item.get("Code", ""),
                    "name": item.get("Name", ""),
                    "held_pct": item.get("SharesHeldPer", ""),
                    "available_pct": item.get("AvailableInvestPer", ""),
                    "upper_limit": item.get("Upperlimit", ""),
                }
            )
        _FOREIGN_HOLDING_CACHE["tw_foreign_holding_top20"] = results
        return results
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return [{"error": str(e)}]


# 台股大盤指數。個股工具都要 ticker，問「台股今天怎樣」時沒有任何工具能回答，
# 模型只好照著 skill 版面硬掰 —— 這支補的就是那個缺口。
TW_MARKET_INDICES = {
    "TAIEX": ("^TWII", "TAIEX Weighted Stock Index (Market)"),
    "TPEX": ("^TWOII", "TPEx Exchange Index (OTC)"),
}


@tool
def tw_market_index() -> dict:
    """獲取台股大盤指數即時行情：加權指數（TAIEX）與櫃買指數（OTC）。

    當用戶問的是整體市場（「台股今天如何」「大盤漲跌」「加權指數多少」）
    而非特定個股時使用。個股請改用 tw_stock_price 或 tw_stock_snapshot。
    """
    import yfinance as yf

    indices = {}
    errors = []

    for key, (symbol, name) in TW_MARKET_INDICES.items():
        try:
            fast_info = yf.Ticker(symbol).fast_info
            current = getattr(fast_info, "last_price", None)
            prev = getattr(fast_info, "previous_close", None)

            if current is None:
                errors.append(f"{name}: failed to fetch quote")
                continue

            entry = {"name": name, "symbol": symbol, "price": round(float(current), 2)}
            if prev:
                change = float(current) - float(prev)
                entry["prev_close"] = round(float(prev), 2)
                entry["change"] = round(change, 2)
                entry["change_pct"] = round(change / float(prev) * 100, 2)
            indices[key] = entry
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 - 單一指數失敗不應讓整支工具失敗
            errors.append(f"{name}: {type(exc).__name__}")

    if not indices:
        return {
            "error": "Failed to fetch TW market indices",
            "details": errors,
            "source": "yfinance",
        }

    result = {"indices": indices, "source": "yfinance"}
    if errors:
        result["partial_errors"] = errors
    return result
