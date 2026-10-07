"""
yahoo_provider.py — YahooFinanceProvider 實作

包裝 yfinance 為 StockDataProvider 介面，提供所有市場通用的基礎資料：
- get_price: fast_info → ticker.info → history 三層 fallback
- get_technicals: RSI(14) / MACD / MA20 / MA50 / 52W
- get_fundamentals: ticker.info 完整基本面（PE/Beta/EPS/股息/分析師目標等）
- get_news: ticker.news；它回空時改打 Yahoo 搜尋 API 的 news 區塊（見 _search_news）
- get_klines: ticker.history

設計上完全 sync，呼叫端如需 async 用 asyncio.to_thread 包裝。
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from utils.lazy_import import lazy_module

from .base_provider import StockDataProvider

yf = lazy_module("yfinance")  # 第一次用到才載入（見 utils/lazy_import.py）

logger = logging.getLogger(__name__)


def rsi_wilder(closes, length: int = 14) -> Optional[float]:
    """RSI（Wilder 平滑）——與 tw_technical_analysis 的 pandas_ta.rsi 同一套算法。

    資料不足 length+1 根、或整段沒有任何漲跌（0/0 無定義）時回 None。
    """
    import pandas_ta as ta

    rsi = ta.rsi(closes, length=length)
    if rsi is None or rsi.empty:
        return None
    val = float(rsi.iloc[-1])
    return round(val, 1) if val == val else None


class YahooFinanceProvider(StockDataProvider):
    """yfinance 通用 Provider — 適用所有市場。"""

    def __init__(self, market: str = "us", decimal_places: int = 2) -> None:
        super().__init__()
        self.market = market
        self.decimal_places = decimal_places

    # ── Price ────────────────────────────────────────────────────────────────
    def get_price(self, symbol: str) -> Dict[str, Any]:
        sym = self.normalize(symbol)
        cache_key = f"price:{sym}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            ticker = yf.Ticker(sym)
            fi = ticker.fast_info

            price = fi.last_price
            prev = fi.previous_close

            # Fallback 1: ticker.info
            if price is None or price != price:
                try:
                    info = ticker.info
                    price = info.get("regularMarketPrice") or info.get("currentPrice")
                    if prev is None:
                        prev = info.get("regularMarketPreviousClose") or info.get(
                            "previousClose"
                        )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    pass

            # Fallback 2: history
            if (price is None or price != price) or prev is None:
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

            if price is None or price != price or price <= 0:
                return {"symbol": sym, "error": f"無法取得 {sym} 報價"}

            dp = self.decimal_places
            price_f = round(float(price), dp)
            prev_f = round(float(prev), dp) if prev else price_f
            change = round(price_f - prev_f, dp)
            change_pct = round((change / prev_f) * 100, 2) if prev_f else 0.0

            try:
                currency = fi.currency or self._default_currency()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                currency = self._default_currency()

            name = self._resolve_name(ticker, sym)

            result = {
                "symbol": sym,
                "name": name,
                "price": price_f,
                "prev_close": prev_f,
                "change": change,
                "changePercent": change_pct,
                "currency": currency,
            }
            self._cache_set(cache_key, result, ttl=60)
            return result
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[YahooProvider] get_price failed {sym}: {e}")
            return {"symbol": sym, "error": str(e)}

    # ── Technicals ───────────────────────────────────────────────────────────
    def get_technicals(self, symbol: str) -> Dict[str, Any]:
        sym = self.normalize(symbol)
        cache_key = f"tech:{sym}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1y", interval="1d")
            if hist.empty or len(hist) < 15:
                return {}

            closes_series = hist["Close"].dropna()
            closes = closes_series.tolist()
            if len(closes) < 15:
                return {}

            # RSI(14) — Wilder 平滑
            rsi = rsi_wilder(closes_series)

            # MACD (EMA12 - EMA26，signal=EMA9)
            ema12 = closes_series.ewm(span=12).mean()
            ema26 = closes_series.ewm(span=26).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9).mean()
            macd_histogram = round(
                float(macd_line.iloc[-1] - signal_line.iloc[-1]), self.decimal_places
            )

            # MA20 / MA50
            ma20 = (
                round(
                    float(closes_series.rolling(20).mean().iloc[-1]),
                    self.decimal_places,
                )
                if len(closes) >= 20
                else None
            )
            ma50 = (
                round(
                    float(closes_series.rolling(50).mean().iloc[-1]),
                    self.decimal_places,
                )
                if len(closes) >= 50
                else None
            )

            highs = [h for h in hist["High"].tolist() if h is not None and h == h]
            lows = [lo for lo in hist["Low"].tolist() if lo is not None and lo == lo]
            high_52w = round(max(highs), self.decimal_places) if highs else None
            low_52w = round(min(lows), self.decimal_places) if lows else None

            result = {
                "rsi": rsi,
                "macd_histogram": macd_histogram,
                "ma20": ma20,
                "ma50": ma50,
                "52w_high": high_52w,
                "52w_low": low_52w,
            }
            self._cache_set(cache_key, result, ttl=300)
            return result
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[YahooProvider] get_technicals failed {sym}: {e}")
            return {}

    # ── Fundamentals ─────────────────────────────────────────────────────────
    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        sym = self.normalize(symbol)
        cache_key = f"fund:{sym}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            ticker = yf.Ticker(sym)
            fi = ticker.fast_info
            info = ticker.info or {}
            result: Dict[str, Any] = {}

            # fast_info (cheap)
            try:
                vol = fi.last_volume
                if vol and vol == vol:
                    result["volume"] = int(vol)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass
            try:
                mc = fi.market_cap
                if mc and mc == mc:
                    result["market_cap"] = int(mc)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass

            def _sf(key: str):
                v = info.get(key)
                return float(v) if v is not None and v == v else None

            def _si(key: str):
                v = info.get(key)
                return int(v) if v is not None and v == v else None

            pe = _sf("trailingPE") or _sf("forwardPE")
            if pe:
                result["pe_ratio"] = round(pe, 2)

            pb = _sf("priceToBook")
            if pb:
                result["pb_ratio"] = round(pb, 2)

            beta = _sf("beta")
            if beta:
                result["beta"] = round(beta, 2)

            dy = _sf("dividendYield")
            if dy:
                result["dividend_yield"] = round(dy * 100, 2)

            eps = _sf("trailingEps") or _sf("forwardEps")
            if eps:
                result["eps"] = round(eps, 2)

            rg = _sf("revenueGrowth")
            if rg:
                result["revenue_growth"] = round(rg * 100, 1)

            eg = _sf("earningsGrowth")
            if eg:
                result["earnings_growth"] = round(eg * 100, 1)

            pm = _sf("profitMargins")
            if pm:
                result["profit_margins"] = round(pm * 100, 1)

            tp = _sf("targetMeanPrice")
            if tp:
                result["target_price"] = round(tp, 2)

            rec = info.get("recommendationKey")
            if rec:
                result["recommendation"] = str(rec).lower()

            sector = info.get("sector")
            if sector:
                result["sector"] = sector

            industry = info.get("industry")
            if industry:
                result["industry"] = industry

            if "avg_volume" not in result:
                av = _si("averageVolume") or _si("averageVolume10days")
                if av:
                    result["avg_volume"] = av

            if "market_cap" not in result:
                mc2 = _si("marketCap")
                if mc2:
                    result["market_cap"] = mc2

            # TTL 6h：fundamentals（PE/PB/EPS）季頻更新，業界慣例（Intrinio 等）為
            # 數天到數週，這裡取保守 6h（使用者一定看到當天資料）。
            self._cache_set(cache_key, result, ttl=21600)
            return result
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[YahooProvider] get_fundamentals failed {sym}: {e}")
            return {}

    # ── News ─────────────────────────────────────────────────────────────────
    def _search_news(self, sym: str, limit: int) -> List[Dict[str, Any]]:
        """Yahoo 搜尋 API 的 news 區塊（免金鑰）。

        yfinance 的 ticker.news 在 2026-10 實測對任何代號都回空（AAPL 也是，美股新聞區因此整個空白）；
        同一組新聞從 query1.finance.yahoo.com/v1/finance/search 的 news 欄位拿得到（正式機器查得到，
        日股搜尋同一支 API 在用）。失敗回空清單，不影響呼叫端。
        """
        try:
            resp = httpx.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": sym, "newsCount": limit, "quotesCount": 0},
                headers={"User-Agent": "Mozilla/5.0 (compatible; CryptoMind/1.0)"},
                timeout=8,
            )
            resp.raise_for_status()
            raw = (resp.json() or {}).get("news") or []
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[YahooProvider] news search failed {sym}: {e}")
            return []
        items: List[Dict[str, Any]] = []
        for item in raw[:limit]:
            title = str(item.get("title") or "").strip()
            url = str(item.get("link") or "").strip()
            if not title or not url.startswith("http"):
                continue
            ts = item.get("providerPublishTime")
            pub_at = int(ts) if isinstance(ts, (int, float)) else 0
            items.append(
                {
                    "symbol": sym,
                    "title": title,
                    "url": url,
                    "publisher": item.get("publisher") or "",
                    "published": pub_at,
                    "pub_str": datetime.fromtimestamp(pub_at, tz=timezone.utc).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                    if pub_at
                    else "",
                }
            )
        return items

    def get_news(self, symbol: str, limit: int = 5) -> List[Dict[str, Any]]:
        sym = self.normalize(symbol)
        cache_key = f"news:{sym}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            ticker = yf.Ticker(sym)
            raw = ticker.news or []
            items: List[Dict[str, Any]] = []
            for item in raw[:limit]:
                content = (
                    item.get("content", {})
                    if isinstance(item.get("content"), dict)
                    else {}
                )
                title = (content.get("title") or item.get("title", "")).strip()
                if not title:
                    continue
                canonical = content.get("canonicalUrl") or {}
                clickthrough = content.get("clickThroughUrl") or {}
                url = (
                    canonical.get("url")
                    or clickthrough.get("url")
                    or item.get("link", "")
                )
                provider = content.get("provider") or {}
                source = provider.get("displayName") or item.get("publisher", "")
                pub_date = content.get("pubDate") or item.get("providerPublishTime")
                pub_str = ""
                pub_at = 0
                if isinstance(pub_date, str):
                    try:
                        dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                        pub_at = int(dt.timestamp())
                        pub_str = dt.strftime("%Y-%m-%d %H:%M")
                    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        pass
                elif isinstance(pub_date, (int, float)):
                    pub_at = int(pub_date)
                    pub_str = datetime.fromtimestamp(pub_at, tz=timezone.utc).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                items.append(
                    {
                        "symbol": sym,
                        "title": title,
                        "url": url,
                        "publisher": source,
                        "published": pub_at,
                        "pub_str": pub_str,
                    }
                )
            if not items:
                items = self._search_news(sym, limit)
            self._cache_set(cache_key, items, ttl=300)
            return items
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[YahooProvider] get_news failed {sym}: {e}")
            # yfinance 自己丟例外時也試一次搜尋 API
            return self._search_news(sym, limit)

    # ── K-line ───────────────────────────────────────────────────────────────
    def get_klines(
        self, symbol: str, interval: str = "1d", limit: int = 200
    ) -> List[Dict[str, Any]]:
        sym = self.normalize(symbol)
        period_map = {"1d": "1y", "1wk": "2y", "1mo": "5y"}
        period = period_map.get(interval, "1y")

        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period=period, interval=interval)
            if hist.empty:
                return []

            klines = []
            for idx, row in hist.iterrows():
                try:
                    klines.append(
                        {
                            "time": idx.strftime("%Y-%m-%d"),
                            "open": round(float(row["Open"]), self.decimal_places),
                            "high": round(float(row["High"]), self.decimal_places),
                            "low": round(float(row["Low"]), self.decimal_places),
                            "close": round(float(row["Close"]), self.decimal_places),
                            "volume": int(row["Volume"]),
                        }
                    )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    continue
            return klines[-limit:]
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[YahooProvider] get_klines failed {sym}: {e}")
            return []

    # ── Snapshot（並行版本，比 sequential 快 4-5 倍）──
    def get_snapshot(self, symbol: str) -> Dict[str, Any]:
        sym = self.normalize(symbol)
        with ThreadPoolExecutor(max_workers=5) as pool:
            f_price = pool.submit(self.get_price, symbol)
            f_tech = pool.submit(self.get_technicals, symbol)
            f_fund = pool.submit(self.get_fundamentals, symbol)
            f_extras = pool.submit(self.get_extras, symbol)
            f_news = pool.submit(self.get_news, symbol, 5)

        return {
            "symbol": sym,
            "market": self.market,
            "price": f_price.result(),
            "technicals": f_tech.result(),
            "fundamentals": f_fund.result(),
            "extras": f_extras.result(),
            "news": f_news.result(),
        }

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _default_currency(self) -> str:
        """各市場預設貨幣。"""
        return {
            "us": "USD",
            "tw": "TWD",
            "hk": "HKD",
            "jp": "JPY",
            "kr": "KRW",
            "in": "INR",
            "cn": "CNY",
        }.get(self.market, "USD")

    def _resolve_name(self, ticker: yf.Ticker, sym: str) -> str:
        """嘗試從 ticker.info 取得公司名稱。"""
        try:
            info = ticker.info
            return info.get("shortName") or info.get("longName") or sym
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return sym
