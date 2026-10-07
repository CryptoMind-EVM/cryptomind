"""SEC EDGAR 公開資料（免金鑰）：美股公司最近的申報文件。

DANNY 2026-09-13 從 public-apis 清單挑的。SEC 要求帶識別用的 User-Agent，沒有會 403。
- ticker → CIK：``https://www.sec.gov/files/company_tickers.json``（一萬多筆，快取 1 天）
- 最近申報：``https://data.sec.gov/submissions/CIK##########.json``（快取 10 分鐘）
- 文件連結：``https://www.sec.gov/Archives/edgar/data/{cik}/{accession 去橫線}/{primaryDocument}``
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from langchain_core.tools import tool

from core.tools.helpers import crypto_misroute_error, is_crypto_symbol

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/include/ticker.txt"  # 150 KB 的「ticker\tcik」；比 company_tickers.json 小五倍
TICKERS_JSON_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"
TIMEOUT = 15.0
TICKERS_TTL = 24 * 3600
SUBMISSIONS_TTL = 600
# 值得講的表格；其他（S-8、SC 13G/A 之類）預設不列，all_forms=True 才全列
NOTABLE_FORMS = {
    "8-K": "Current report – material event (8-K)",
    "10-Q": "Quarterly report (10-Q)",
    "10-K": "Annual report (10-K)",
    "4": "Insider transaction (Form 4)",
    "3": "Initial insider holdings (Form 3)",
    "SC 13D": "5%+ holder, active (13D)",
    "SC 13G": "5%+ holder, passive (13G)",
    "S-1": "Registration statement (S-1)",
    "424B4": "Prospectus (424B4)",
    "DEF 14A": "Proxy statement (DEF 14A)",
    "20-F": "Foreign issuer annual report (20-F)",
    "6-K": "Foreign issuer current report (6-K)",
}
_tickers_cache: Dict[str, Any] = {"at": 0.0, "map": {}}
_submissions_cache: Dict[int, tuple[float, dict]] = {}


def _headers() -> Dict[str, str]:
    ua = (
        os.getenv("SEC_EDGAR_USER_AGENT", "").strip()
        or "CryptoMind/1.0 (brief@getcryptomind.com)"
    )
    return {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}


def ticker_to_cik(symbol: str) -> Optional[int]:
    """大小寫不拘；BRK.B 這種在 SEC 是 BRK-B。快取一天。"""
    sym = (symbol or "").strip().upper().replace(".", "-")
    if not sym:
        return None
    now = time.time()
    if not _tickers_cache["map"] or now - _tickers_cache["at"] > TICKERS_TTL:
        _tickers_cache["map"] = _load_ticker_map()
        _tickers_cache["at"] = now
    return _tickers_cache["map"].get(sym)


def _load_ticker_map() -> Dict[str, int]:
    """ticker.txt 優先（小），失敗退 company_tickers.json；再失敗留舊表（呼叫端會重試）。"""
    try:
        resp = httpx.get(TICKERS_URL, headers=_headers(), timeout=30.0)
        resp.raise_for_status()
        out: Dict[str, int] = {}
        for line in resp.text.splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 2 and parts[1].isdigit():
                out[parts[0].upper()] = int(parts[1])
        if out:
            return out
    except Exception as exc:  # noqa: BLE001
        logger.info("[sec] ticker.txt failed: %s", type(exc).__name__)
    resp = httpx.get(TICKERS_JSON_URL, headers=_headers(), timeout=30.0)
    resp.raise_for_status()
    rows = resp.json()
    return {
        str(r.get("ticker", "")).upper(): int(r["cik_str"])
        for r in (rows.values() if isinstance(rows, dict) else rows)
        if r.get("ticker") and r.get("cik_str") is not None
    }


def fetch_submissions(cik: int) -> dict:
    now = time.time()
    hit = _submissions_cache.get(cik)
    if hit and now - hit[0] < SUBMISSIONS_TTL:
        return hit[1]
    resp = httpx.get(
        SUBMISSIONS_URL.format(cik=cik), headers=_headers(), timeout=TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    _submissions_cache[cik] = (now, data)
    return data


def recent_filings(
    data: dict, *, limit: int = 10, all_forms: bool = False
) -> List[Dict[str, Any]]:
    """submissions JSON → 最近 N 筆（純函式）。"""
    cik = int(data.get("cik") or 0)
    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    out: List[Dict[str, Any]] = []
    for i, form in enumerate(forms):
        form = str(form or "").strip()
        base = form.replace("/A", "")
        if not all_forms and base not in NOTABLE_FORMS:
            continue
        accession = str((recent.get("accessionNumber") or [""])[i] or "")
        doc = str((recent.get("primaryDocument") or [""])[i] or "")
        items = (recent.get("items") or [""])[i] if recent.get("items") else ""
        out.append(
            {
                "form": form,
                "label": NOTABLE_FORMS.get(base, form),
                "filed": (recent.get("filingDate") or [""])[i],
                "report_date": (recent.get("reportDate") or [""])[i] or None,
                "description": (recent.get("primaryDocDescription") or [""])[i] or "",
                "items": items
                or None,  # 8-K 的 item 代號（2.02 = 財報、5.02 = 高層異動…）
                "url": ARCHIVE_URL.format(
                    cik=cik, accession=accession.replace("-", ""), doc=doc
                )
                if accession and doc
                else None,
            }
        )
        if len(out) >= limit:
            break
    return out


@tool("sec_filings")
def sec_filings(symbol: str, limit: int = 10, all_forms: bool = False) -> Dict:
    """查美股公司在 SEC EDGAR 最近的申報文件（8-K 重大事件、10-Q／10-K 財報、Form 4 內部人交易、
    13D／13G 大股東、S-1 等），每筆附官方連結。適合「這家公司最近有什麼公告／內部人有沒有在賣」。

    Args:
        symbol: 美股代號（帶點的 class 股如 XXX.B 也可）。
        limit: 最多幾筆（預設 10）。
        all_forms: True 時不過濾表格種類。
    """
    if is_crypto_symbol(symbol):
        return crypto_misroute_error(symbol, "US stock")
    try:
        cik = ticker_to_cik(symbol)
        if not cik:
            return {
                "symbol": symbol,
                "error": "Ticker not found in SEC EDGAR (only companies filing in the US)",
            }
        data = fetch_submissions(cik)
        rows = recent_filings(
            data, limit=max(1, min(int(limit or 10), 30)), all_forms=bool(all_forms)
        )
        return {
            "symbol": symbol.upper(),
            "company": data.get("name"),
            "cik": cik,
            "sic_description": data.get("sicDescription"),
            "fiscal_year_end": data.get("fiscalYearEnd"),
            "filings": rows,
            "count": len(rows),
            "source": "SEC EDGAR (data.sec.gov)",
        }
    except httpx.HTTPStatusError as exc:
        return {"symbol": symbol, "error": f"SEC EDGAR HTTP {exc.response.status_code}"}
    except Exception as exc:  # noqa: BLE001
        logger.info("[sec_filings] %s failed: %s", symbol, type(exc).__name__)
        return {"symbol": symbol, "error": "SEC EDGAR is temporarily unavailable"}
