"""
Taiwan Stock Symbol Resolver

Resolves Chinese names, English tickers, or bare codes to Yahoo Finance format.
Examples: "[公司名稱]" → "[代號].TW", "[英文簡稱]" → "[代號].TW", "[代號]" → "[代號].TW"

Data sources:
  - TWSE: openapi.twse.com.tw     (上市，中文欄位名)
  - TPEX: www.tpex.org.tw/openapi (上櫃，英文欄位名)
Cache: in-memory, 24h TTL
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)


class TWSymbolResolver:
    TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    # TPEx 換過網域與 schema：舊的 openapi.tpex.org.tw 已下線（DNS NXDOMAIN，
    # 線上每次啟動都是 fetch error → 上櫃股票整批進不了模糊比對清單）。
    # 新端點在 www.tpex.org.tw/openapi/v1，欄位名也從中文改成英文。
    TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
    # 各來源的 (代號, 簡稱, 英文簡稱) 欄位名
    TWSE_FIELDS = ("公司代號", "公司簡稱", "英文簡稱")
    TPEX_FIELDS = ("SecuritiesCompanyCode", "CompanyAbbreviation", "Symbol")
    CACHE_TTL_HOURS = 24
    # 有來源掛掉時的短快取：不要帶著半份清單（例如整個上櫃不見）撐滿 24 小時
    PARTIAL_TTL_MINUTES = 15
    FUZZY_THRESHOLD = 80

    def __init__(self):
        self._cache: Optional[list] = None
        self._cache_expiry: Optional[datetime] = None

    def resolve(self, input_str: str) -> Optional[str]:
        """Resolve input to Yahoo Finance TW ticker (e.g., '[代號].TW').
        Returns None if no match found."""
        result = self.resolve_with_metadata(input_str)
        return result["ticker"] if result else None

    def resolve_with_metadata(self, input_str: str) -> Optional[dict]:
        """Resolve input and return ticker with match metadata."""
        s = input_str.strip()

        # Rule 1: already has suffix
        upper = s.upper()
        if upper.endswith(".TW") or upper.endswith(".TWO"):
            return {
                "ticker": upper,
                "match_type": "suffix",
                "input": s,
            }

        # Rule 2: pure digit code (4–6 digits) → assume listed stock
        if s.isdigit() and 4 <= len(s) <= 6:
            return {
                "ticker": f"{s}.TW",
                "match_type": "code",
                "input": s,
            }

        # Rule 3: fuzzy match against full name list
        stock_list = self._get_stock_list()
        if stock_list:
            fuzzy_match = self._fuzzy_match(s, stock_list)
            if fuzzy_match:
                return {
                    "ticker": fuzzy_match["ticker"],
                    "match_type": "fuzzy",
                    "matched_text": fuzzy_match["matched_text"],
                    "score": fuzzy_match["score"],
                    "input": s,
                }

        return None

    def _get_stock_list(self) -> list:
        """Return cached stock list, refreshing if stale."""
        now = datetime.now(timezone.utc)
        if (
            self._cache is not None
            and self._cache_expiry is not None
            and now < self._cache_expiry
        ):
            return self._cache

        stocks = []
        failed = False
        sources = [
            (self.TWSE_URL, ".TW", self.TWSE_FIELDS),
            (self.TPEX_URL, ".TWO", self.TPEX_FIELDS),
        ]
        for url, suffix, (f_code, f_name, f_eng) in sources:
            try:
                resp = httpx.get(url, timeout=10)
                if resp.status_code != 200:
                    failed = True
                else:
                    for item in resp.json():
                        # 值可能是 None；英文簡稱常帶全形空白，strip() 會一併清掉
                        code = (item.get(f_code) or "").strip()
                        name = (item.get(f_name) or "").strip()
                        eng = (item.get(f_eng) or "").strip()
                        if code and name:
                            stocks.append(
                                {
                                    "code": code,
                                    "name": name,
                                    "eng": eng,
                                    "ticker": f"{code}{suffix}",
                                }
                            )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                failed = True
                logger.warning(f"[TWSymbolResolver] fetch error {url}: {e}")

        if stocks:
            self._cache = stocks
            # 有來源失敗就只快取 15 分鐘，讓下次呼叫有機會把缺的市場補回來，
            # 而不是帶著半份清單（例如整個上櫃不見）撐滿一天。
            self._cache_expiry = now + (
                timedelta(minutes=self.PARTIAL_TTL_MINUTES)
                if failed
                else timedelta(hours=self.CACHE_TTL_HOURS)
            )

        return stocks or []

    def _fuzzy_match(self, query: str, stock_list: list) -> Optional[dict]:
        """Return best-match metadata or None if score < threshold."""
        target_map: dict[str, dict] = {}
        for s in stock_list:
            target_map[s["name"]] = s
            if s["eng"]:
                target_map[s["eng"]] = s

        result = process.extractOne(
            query,
            list(target_map.keys()),
            scorer=fuzz.WRatio,
            score_cutoff=self.FUZZY_THRESHOLD,
        )
        if result:
            match_str, _score, _idx = result
            return {
                "ticker": target_map[match_str]["ticker"],
                "matched_text": match_str,
                "score": _score,
            }

        return None
