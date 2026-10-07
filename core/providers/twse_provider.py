"""
twse_provider.py — TWSEProvider 實作

台股專屬 Provider。組合 YahooFinanceProvider 取得基礎價格 / 技術指標 / 通用基本面，
並 override get_extras() 補上 TWSE OpenAPI 特有資料：
- 三大法人買賣超（外資、投信、自營）
- 月營收（年增率、月增率）
- 本益比 / 股價淨值比 / 殖利率（TWSE 官方）
- 股利分派
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import httpx

from .base_provider import StockDataProvider
from .yahoo_provider import YahooFinanceProvider

logger = logging.getLogger(__name__)

TWSE_OPENAPI_BASE = "https://openapi.twse.com.tw/v1"
TWSE_RWD_BASE = "https://www.twse.com.tw/rwd/zh"
_TWSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.twse.com.tw/",
}


class TWSEProvider(StockDataProvider):
    """台股 Provider — Yahoo 基礎 + TWSE 特有 extras。"""

    market = "tw"

    def __init__(self) -> None:
        super().__init__()
        # 組合而非繼承：Yahoo 處理價格、技術指標、基本面、新聞
        self._yf = YahooFinanceProvider(market="tw", decimal_places=2)

    # ── 委派給 Yahoo ─────────────────────────────────────────────────────────
    def get_price(self, symbol: str) -> Dict[str, Any]:
        return self._yf.get_price(symbol)

    def get_technicals(self, symbol: str) -> Dict[str, Any]:
        return self._yf.get_technicals(symbol)

    def get_news(self, symbol: str, limit: int = 5) -> List[Dict[str, Any]]:
        return self._yf.get_news(symbol, limit=limit)

    def get_klines(
        self, symbol: str, interval: str = "1d", limit: int = 200
    ) -> List[Dict[str, Any]]:
        return self._yf.get_klines(symbol, interval=interval, limit=limit)

    def normalize(self, symbol: str) -> str:
        return self._yf.normalize(symbol)

    # ── 基本面：先 Yahoo，再用 TWSE 官方 PE/PB/殖利率覆蓋 ─────────────────────
    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        base = self._yf.get_fundamentals(symbol)
        code = self._extract_code(symbol)

        twse_pe = self._fetch_twse_pe(code)
        # TWSE 官方資料優先（更準確）
        if twse_pe.get("pe_ratio") is not None:
            base["pe_ratio"] = twse_pe["pe_ratio"]
        if twse_pe.get("pb_ratio") is not None:
            base["pb_ratio"] = twse_pe["pb_ratio"]
        if twse_pe.get("dividend_yield") is not None:
            base["dividend_yield"] = twse_pe["dividend_yield"]
        return base

    # ── Extras：TWSE 專屬資料 ────────────────────────────────────────────────
    def get_extras(self, symbol: str) -> Dict[str, Any]:
        """三大法人 + 月營收 + 股利分派。並行抓取。"""
        sym = self.normalize(symbol)
        cache_key = f"extras:{sym}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        code = self._extract_code(symbol)
        result: Dict[str, Any] = {"source": "TWSE OpenAPI"}

        with ThreadPoolExecutor(max_workers=3) as pool:
            f_inst = pool.submit(self._fetch_institutional, code)
            f_rev = pool.submit(self._fetch_monthly_revenue, code)
            f_div = pool.submit(self._fetch_dividend, code)

        inst = f_inst.result()
        rev = f_rev.result()
        div = f_div.result()

        if inst:
            result["institutional"] = inst
        if rev:
            result["monthly_revenue"] = rev
        if div:
            result["dividend"] = div

        # TTL 6h：extras（法人+月營收+股息 bundle）日頻更新
        self._cache_set(cache_key, result, ttl=21600)
        return result

    # ── Snapshot：覆蓋成並行版本 ─────────────────────────────────────────────
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
            "market": "tw",
            "price": f_price.result(),
            "technicals": f_tech.result(),
            "fundamentals": f_fund.result(),
            "extras": f_extras.result(),
            "news": f_news.result(),
        }

    # ──────────────────────────────────────────────────────────────────────
    # TW-specific 公開方法（供 tw_stock_tools @tool 函數使用，避免重複實作）
    # ──────────────────────────────────────────────────────────────────────

    def get_twse_pe_detail(self, symbol: str) -> Dict[str, Any]:
        """個股 PE/殖利率/PB 詳細資料（TWSE BWIBBU_d/BWIBBU_ALL）。

        回傳結構與原 tw_pe_ratio tool 相同：
          {code, name, date, pe_ratio, dividend_yield, pb_ratio, dividend_year, source}
        """
        code = self._extract_code(symbol)
        cache_key = f"twse_pe_detail:{code}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        endpoints = [
            f"{TWSE_OPENAPI_BASE}/exchangeReport/BWIBBU_d",
            f"{TWSE_OPENAPI_BASE}/exchangeReport/BWIBBU_ALL",
        ]
        for url in endpoints:
            try:
                resp = httpx.get(url, timeout=12, headers=_TWSE_HEADERS)
                if resp.status_code != 200:
                    continue
                data = resp.json() or []
                for row in data:
                    if str(row.get("Code", "")).strip() == code:
                        result = {
                            "code": row.get("Code", code),
                            "name": row.get("Name", ""),
                            "date": row.get("Date", ""),
                            "pe_ratio": row.get("PEratio", "N/A"),
                            "dividend_yield": row.get("DividendYield", "N/A"),
                            "pb_ratio": row.get("PBratio", "N/A"),
                            "dividend_year": row.get("DividendYear", ""),
                            "source": "TWSE OpenAPI",
                        }
                        # TTL 6h：PE/殖利率/PB 日頻更新（業界 fundamentals 慣例數天-數週，取保守 6h）
                        self._cache_set(cache_key, result, ttl=21600)
                        return result
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.debug(f"[TWSEProvider] PE detail fetch failed {url}: {e}")
                continue

        result = {"code": code, "error": f"查無 {code} 的本益比資料（可能非上市股票）"}
        self._cache_set(cache_key, result, ttl=300)
        return result

    def get_twse_institutional_detail(self, symbol: str) -> Dict[str, Any]:
        """三大法人籌碼詳細資料（TWSE T86）。

        回傳結構與原 tw_institutional tool 相同：
          {ticker, date, foreign_net, investment_trust, dealer_net, total_3party_net, source}

        TTL 6h：法人買賣超日頻更新（_fetch_institutional 會掃 6 個交易日頗重，
        加快取避免重複掃；業界 daily 資料快取數小時是常態）。
        """
        ticker = self.normalize(symbol)
        code = self._extract_code(symbol)
        cache_key = f"twse_inst_detail:{code}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        inst = self._fetch_institutional(code)
        if inst:
            result = {
                "ticker": ticker,
                "date": inst.get("date"),
                "foreign_net": inst.get("foreign_net"),
                "investment_trust": inst.get("investment_trust"),
                "dealer_net": inst.get("dealer_net"),
                "total_3party_net": inst.get("total_3party_net"),
                "source": "TWSE T86",
            }
        else:
            result = {
                "ticker": ticker,
                "note": "TWSE 法人 API 目前無法取得資料，可能為非交易日或資料尚未更新。",
                "source": "TWSE T86 (失敗)",
            }
        self._cache_set(cache_key, result, ttl=21600)
        return result

    def get_twse_monthly_revenue_detail(self, symbol: str = "") -> List[Dict[str, Any]]:
        """月營收詳細資料（TWSE t187ap05_L）。
        symbol 為空時回傳全市場前 30 筆。

        回傳結構與原 tw_monthly_revenue tool 相同（list of dicts）。
        """
        code = self._extract_code(symbol) if symbol else ""
        cache_key = f"twse_revenue_detail:{code or 'all'}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            resp = httpx.get(
                f"{TWSE_OPENAPI_BASE}/opendata/t187ap05_L",
                timeout=15,
                headers=_TWSE_HEADERS,
            )
            data = resp.json() if resp.status_code == 200 else []
            if code:
                data = [d for d in (data or []) if d.get("公司代號", "") == code]
            else:
                data = (data or [])[:30]
            results = []
            for item in data:
                results.append(
                    {
                        "code": item.get("公司代號", ""),
                        "name": item.get("公司名稱", ""),
                        "industry": item.get("產業別", ""),
                        "ym": item.get("資料年月", ""),
                        "current_revenue": item.get("營業收入-當月營收", ""),
                        "mom_pct": item.get("營業收入-上月比較增減(%)", ""),
                        "yoy_pct": item.get("營業收入-去年當月增減(%)", ""),
                        "ytd_revenue": item.get("累計營業收入-當月累計營收", ""),
                        "ytd_yoy_pct": item.get("累計營業收入-前期比較增減(%)", ""),
                    }
                )
            # TTL 6h：股息 / 月營收 日頻更新
            self._cache_set(cache_key, results, ttl=21600)
            return results
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[TWSEProvider] monthly revenue detail failed: {e}")
            return [{"error": str(e)}]

    def get_twse_dividend_detail(self, symbol: str = "") -> List[Dict[str, Any]]:
        """股利分派詳細資料（TWSE t187ap45_L）。
        symbol 為空時回傳近期前 30 筆。

        回傳結構與原 tw_dividend_info tool 相同（list of dicts）。
        """
        code = self._extract_code(symbol) if symbol else ""
        cache_key = f"twse_dividend_detail:{code or 'all'}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            resp = httpx.get(
                f"{TWSE_OPENAPI_BASE}/opendata/t187ap45_L",
                timeout=15,
                headers=_TWSE_HEADERS,
            )
            data = resp.json() if resp.status_code == 200 else []
            if code:
                data = [d for d in (data or []) if d.get("公司代號", "") == code]
            else:
                data = (data or [])[:30]
            results = []
            for item in data:
                results.append(
                    {
                        "code": item.get("公司代號", ""),
                        "name": item.get("公司名稱", ""),
                        "year": item.get("股利年度", ""),
                        "progress": item.get("決議（擬議）進度", ""),
                        "board_date": item.get("董事會（擬議）股利分派日", ""),
                        "shareholder_mtg": item.get("股東會日期", ""),
                        "cash_dividend": item.get(
                            "股東配發-盈餘分配之現金股利(元/股)", ""
                        ),
                        "stock_dividend": item.get(
                            "股東配發-盈餘轉增資配股(元/股)", ""
                        ),
                    }
                )
            # TTL 6h：股息 / 月營收 日頻更新
            self._cache_set(cache_key, results, ttl=21600)
            return results
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[TWSEProvider] dividend detail failed: {e}")
            return [{"error": str(e)}]

    # ── TWSE OpenAPI fetchers (private) ─────────────────────────────────────
    @staticmethod
    def _extract_code(symbol: str) -> str:
        """從 '2330.TW' 或 '2330' 取出純代號 '2330'。"""
        return symbol.strip().upper().split(".")[0]

    def _fetch_twse_pe(self, code: str) -> Dict[str, Any]:
        """TWSE 個股本益比 / 殖利率 / PB（BWIBBU_d / BWIBBU_ALL）。"""
        cache_key = f"twse_pe:{code}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        endpoints = [
            f"{TWSE_OPENAPI_BASE}/exchangeReport/BWIBBU_d",
            f"{TWSE_OPENAPI_BASE}/exchangeReport/BWIBBU_ALL",
        ]
        result: Dict[str, Any] = {}
        for url in endpoints:
            try:
                resp = httpx.get(url, timeout=12, headers=_TWSE_HEADERS)
                if resp.status_code != 200:
                    continue
                data = resp.json() or []
                for row in data:
                    if str(row.get("Code", "")).strip() == code:
                        pe = self._safe_float(row.get("PEratio") or row.get("本益比"))
                        pb = self._safe_float(
                            row.get("PBratio") or row.get("股價淨值比")
                        )
                        dy = self._safe_float(
                            row.get("DividendYield") or row.get("殖利率(%)")
                        )
                        if pe is not None:
                            result["pe_ratio"] = pe
                        if pb is not None:
                            result["pb_ratio"] = pb
                        if dy is not None:
                            result["dividend_yield"] = dy
                        break
                if result:
                    break
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.debug(f"[TWSEProvider] PE fetch failed {url}: {e}")
                continue

        # TTL 6h：PE/PB/殖利率 日頻更新
        self._cache_set(cache_key, result, ttl=21600)
        return result

    def _fetch_institutional(self, code: str) -> Dict[str, Any]:
        """T86：三大法人買賣超。掃過去 6 個交易日。"""
        for days_back in range(0, 6):
            try:
                target_date = date.today() - timedelta(days=days_back)
                if target_date.weekday() >= 5:
                    continue
                date_str = target_date.strftime("%Y%m%d")
                url = (
                    f"{TWSE_RWD_BASE}/fund/T86"
                    f"?response=json&date={date_str}&selectType=ALLBUT0999"
                )
                resp = httpx.get(url, timeout=12, headers=_TWSE_HEADERS)
                if resp.status_code != 200:
                    continue
                payload = resp.json()
                if payload.get("stat") != "OK":
                    continue
                fields = payload.get("fields", [])
                rows = payload.get("data", [])
                if len(fields) < 19 or not rows:
                    continue

                matching = [r for r in rows if len(r) >= 19 and r[0].strip() == code]
                if not matching:
                    continue

                r = matching[0]
                return {
                    "date": payload.get("date", date_str),
                    "foreign_net": self._parse_inst_number(r[4]),
                    "investment_trust": self._parse_inst_number(r[10]),
                    "dealer_net": self._parse_inst_number(r[11]),
                    "total_3party_net": self._parse_inst_number(r[18]),
                }
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.debug(f"[TWSEProvider] T86 fetch failed: {e}")
                continue
        return {}

    def _fetch_monthly_revenue(self, code: str) -> Optional[Dict[str, Any]]:
        """月營收 t187ap05_L。"""
        try:
            resp = httpx.get(
                f"{TWSE_OPENAPI_BASE}/opendata/t187ap05_L",
                timeout=15,
                headers=_TWSE_HEADERS,
            )
            if resp.status_code != 200:
                return None
            data = resp.json() or []
            for row in data:
                if str(row.get("公司代號", "")).strip() == code:
                    return {
                        "year_month": row.get("資料年月"),
                        "revenue": row.get("當月營收"),
                        "yoy_pct": row.get("去年同月增減(%)"),
                        "mom_pct": row.get("上月比較增減(%)"),
                    }
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(f"[TWSEProvider] monthly revenue failed: {e}")
        return None

    def _fetch_dividend(self, code: str) -> Optional[Dict[str, Any]]:
        """股利分派 t187ap45_L。"""
        try:
            resp = httpx.get(
                f"{TWSE_OPENAPI_BASE}/opendata/t187ap45_L",
                timeout=15,
                headers=_TWSE_HEADERS,
            )
            if resp.status_code != 200:
                return None
            data = resp.json() or []
            for row in data:
                if str(row.get("公司代號", "")).strip() == code:
                    return {
                        "cash_dividend": row.get("現金股利"),
                        "stock_dividend": row.get("股票股利"),
                        "ex_date": row.get("除息交易日"),
                    }
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(f"[TWSEProvider] dividend failed: {e}")
        return None

    # ── Helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _safe_float(v: Any) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            f = float(str(v).replace(",", ""))
            if f != f:  # NaN
                return None
            return round(f, 2)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_inst_number(s: Any) -> Optional[int]:
        """解析法人欄位（含逗號的字串），可能是負值。"""
        if s is None:
            return None
        try:
            return int(str(s).replace(",", ""))
        except (ValueError, TypeError):
            return None
