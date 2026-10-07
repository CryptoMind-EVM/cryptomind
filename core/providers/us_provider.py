"""
us_provider.py — USProvider 實作

美股專屬 Provider。組合 YahooFinanceProvider 取得基礎數據，並 override：
- get_extras():  機構持倉 + 內部人交易（Yahoo Finance 美股獨有）
- get_earnings(): 財報日曆 + 歷史 EPS
- get_price():    若 FINNHUB_API_KEY 存在則優先用 Finnhub（更穩定），否則 Yahoo fallback

設計上保留與現有 us_data_provider.py 同等的功能集，
但用統一 sync 介面（asyncio.to_thread 由呼叫方處理）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import httpx

from utils.lazy_import import lazy_module

from .base_provider import StockDataProvider
from .yahoo_provider import YahooFinanceProvider

yf = lazy_module("yfinance")  # 第一次用到才載入（見 utils/lazy_import.py）

logger = logging.getLogger(__name__)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"


class USProvider(StockDataProvider):
    """美股 Provider — Yahoo 基礎 + 機構持倉/內部人交易/財報 + Finnhub fallback。"""

    market = "us"

    def __init__(self) -> None:
        super().__init__()
        self._yf = YahooFinanceProvider(market="us", decimal_places=2)

    # ── 委派 ─────────────────────────────────────────────────────────────────
    def get_technicals(self, symbol: str) -> Dict[str, Any]:
        return self._yf.get_technicals(symbol)

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        return self._yf.get_fundamentals(symbol)

    def get_news(self, symbol: str, limit: int = 5) -> List[Dict[str, Any]]:
        return self._yf.get_news(symbol, limit=limit)

    def get_klines(
        self, symbol: str, interval: str = "1d", limit: int = 200
    ) -> List[Dict[str, Any]]:
        return self._yf.get_klines(symbol, interval=interval, limit=limit)

    def normalize(self, symbol: str) -> str:
        return self._yf.normalize(symbol)

    # ── Price：Finnhub 優先 + Yahoo fallback ────────────────────────────────
    def get_price(self, symbol: str) -> Dict[str, Any]:
        sym = self.normalize(symbol)
        cache_key = f"price:{sym}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        # 1. 試 Finnhub（若有 key）
        if FINNHUB_API_KEY:
            try:
                resp = httpx.get(
                    f"{FINNHUB_BASE}/quote",
                    params={"symbol": sym, "token": FINNHUB_API_KEY},
                    timeout=8,
                )
                if resp.status_code == 200:
                    data = resp.json() or {}
                    current = data.get("c")
                    prev = data.get("pc")
                    if current and prev:
                        change = round(current - prev, 2)
                        change_pct = round((change / prev) * 100, 2) if prev else 0.0
                        result = {
                            "symbol": sym,
                            "name": self._yf._resolve_name(yf.Ticker(sym), sym),
                            "price": round(current, 2),
                            "prev_close": round(prev, 2),
                            "change": change,
                            "changePercent": change_pct,
                            "currency": "USD",
                            "source": "finnhub",
                        }
                        self._cache_set(cache_key, result, ttl=60)
                        return result
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.debug(
                    f"[USProvider] Finnhub failed for {sym}, fallback to Yahoo: {e}"
                )

        # 2. Yahoo fallback
        result = self._yf.get_price(symbol)
        if "source" not in result and "error" not in result:
            result["source"] = "yahoo"
        return result

    # ── Extras：機構持倉 + 內部人交易 ───────────────────────────────────────
    def get_extras(self, symbol: str) -> Dict[str, Any]:
        sym = self.normalize(symbol)
        cache_key = f"extras:{sym}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_inst = pool.submit(self._fetch_institutional, sym)
            f_insider = pool.submit(self._fetch_insider, sym)

        result: Dict[str, Any] = {}
        inst = f_inst.result()
        insider = f_insider.result()
        if inst:
            result["institutional_holders"] = inst
        if insider:
            result["insider_transactions"] = insider

        self._cache_set(cache_key, result, ttl=86400)  # 24h
        return result

    # ── Earnings：財報日曆 + 歷史 EPS ───────────────────────────────────────
    def get_earnings(self, symbol: str) -> Dict[str, Any]:
        sym = self.normalize(symbol)
        cache_key = f"earnings:{sym}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        result: Dict[str, Any] = {
            "symbol": sym,
            "next_earnings_date": None,
            "next_earnings_date_str": None,
            "earnings_history": [],
        }
        try:
            ticker = yf.Ticker(sym)
            earnings_calendar = ticker.earnings_dates
            if earnings_calendar is not None and len(earnings_calendar) > 0:
                try:
                    next_date = earnings_calendar.index[0]
                    if hasattr(next_date, "strftime"):
                        result["next_earnings_date"] = next_date.strftime("%Y-%m-%d")
                        result["next_earnings_date_str"] = next_date.strftime(
                            "%Y 年 %m 月 %d 日"
                        )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    pass

                try:
                    for _, row in earnings_calendar.head(4).iterrows():
                        est = row.get("EPS Estimate")
                        act = row.get("Reported EPS")
                        item = {
                            "eps_estimate": est,
                            "eps_actual": act,
                            "surprise": row.get("Surprise(%)"),
                        }
                        if est and act:
                            try:
                                item["surprise_percent"] = round(
                                    ((act - est) / est) * 100, 2
                                )
                            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                                raise
                            except Exception:
                                pass
                        result["earnings_history"].append(item)
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    pass
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[USProvider] earnings failed {sym}: {e}")

        self._cache_set(cache_key, result, ttl=86400)
        return result

    # ── Snapshot：並行 ──────────────────────────────────────────────────────
    def get_snapshot(self, symbol: str) -> Dict[str, Any]:
        sym = self.normalize(symbol)
        with ThreadPoolExecutor(max_workers=6) as pool:
            f_price = pool.submit(self.get_price, symbol)
            f_tech = pool.submit(self.get_technicals, symbol)
            f_fund = pool.submit(self.get_fundamentals, symbol)
            f_extras = pool.submit(self.get_extras, symbol)
            f_earn = pool.submit(self.get_earnings, symbol)
            f_news = pool.submit(self.get_news, symbol, 5)

        return {
            "symbol": sym,
            "market": "us",
            "price": f_price.result(),
            "technicals": f_tech.result(),
            "fundamentals": f_fund.result(),
            "extras": f_extras.result(),
            "earnings": f_earn.result(),
            "news": f_news.result(),
        }

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _fetch_institutional(self, sym: str) -> Optional[Dict[str, Any]]:
        try:
            ticker = yf.Ticker(sym)
            df = ticker.institutional_holders
            if df is None or len(df) == 0:
                return None

            holders = []
            total_shares = 0
            for _, row in df.iterrows():
                shares = row.get("Shares", 0) or 0
                holders.append(
                    {
                        "holder": row.get("Holder", ""),
                        "shares": int(shares) if shares else 0,
                        "date_reported": self._fmt_date(row.get("Date Reported", "")),
                        "percent_out": row.get("% Out", 0),
                        "value": row.get("Value", 0),
                    }
                )
                total_shares += shares if shares else 0

            data = {
                "holders": holders,
                "total_shares_held": int(total_shares),
                "percent_held": 0,
            }

            try:
                info = ticker.info
                shares_outstanding = info.get("sharesOutstanding", 0) or 0
                if shares_outstanding:
                    data["percent_held"] = round(
                        (total_shares / shares_outstanding) * 100, 2
                    )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass

            return data
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(f"[USProvider] institutional failed {sym}: {e}")
            return None

    def _fetch_insider(self, sym: str) -> Optional[Dict[str, Any]]:
        try:
            ticker = yf.Ticker(sym)
            df = ticker.insider_transactions
            if df is None or len(df) == 0:
                return None

            txns = []
            for _, row in df.head(10).iterrows():
                txns.append(
                    {
                        "insider": row.get("Insider", ""),
                        "relation": row.get("Relation", ""),
                        "date": self._fmt_date(row.get("Latest Trans Date", "")),
                        "transaction": row.get("Transaction", ""),
                        "shares": row.get("Shares", 0),
                        "value": row.get("Value", 0),
                    }
                )
            return {"transactions": txns}
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(f"[USProvider] insider failed {sym}: {e}")
            return None

    @staticmethod
    def _fmt_date(v: Any) -> str:
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d")
        return str(v) if v is not None else ""
