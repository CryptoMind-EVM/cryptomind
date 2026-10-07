"""總經事件日曆——FOMC 決議日、美國 CPI、非農（都是官方排程，不是預測）。

資料源與退路：
- FOMC：federalreserve.gov 的 fomccalendars.htm（年份面板 → 月／日區間，決議日＝
  區間最後一天），解析結果快取一天；抓不到或解析為零筆就用下方靜態表。
- CPI／非農：BLS 網站對非瀏覽器請求回 403，所以用靜態表（抄自 BLS 公告的年度
  排程）；設了 ``FRED_SERVICE_API_KEY`` 時改抓 FRED release dates 覆蓋
  （FRED release_id：10＝Consumer Price Index、50＝Employment Situation）。

靜態表要每年補：``tests/test_market_events.py`` 會在涵蓋不到「今天＋30 天」時變紅，
失敗訊息寫了去哪抄、改哪個常數。BLS 通常秋天才公布下一年排程；還沒公布就不要推算日期。
有 FRED key 時線上以 FRED 為準（靜態表只當退路）：BLS 一公布，FRED 就會帶到下一年。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_CACHE_KEY = "market_events:fomc:v1"
FOMC_CACHE_TTL_SECONDS = 24 * 3600
FRED_RELEASE_URL = "https://api.stlouisfed.org/fred/release/dates"
FRED_RELEASE_IDS = {"cpi": 10, "nfp": 50}
FETCH_TIMEOUT_SECONDS = 20
DEFAULT_HORIZON_DAYS = 45

CODE_FOMC = "fomc"
CODE_CPI = "cpi"
CODE_NFP = "nfp"

# (month, decision_day)；來源 federalreserve.gov 2026-09-12 抄錄（* 為含 SEP 的會議，
# 不影響日期）。決議日＝兩天會議的第二天。2026-09-24 再對官網：2027 年八場一致。
FOMC_STATIC: Dict[int, List[tuple[int, int]]] = {
    2026: [(1, 28), (3, 18), (4, 29), (6, 17), (7, 29), (9, 16), (10, 28), (12, 9)],
    2027: [(1, 27), (3, 17), (4, 28), (6, 9), (7, 28), (9, 15), (10, 27), (12, 8)],
}
# BLS Consumer Price Index 發布日（08:30 ET），2026-09-12 抄錄
# 來源 https://www.bls.gov/schedule/news_release/cpi.htm（curl 會 403，用瀏覽器開）；
# 可抓的鏡像 https://fred.stlouisfed.org/releases/calendar?rid=10&view=year&vs=2026-01-01&ve=2026-12-31
# 2026-09-24 核對：兩邊 2026 年一致；BLS、FRED、OMB（PFEI 排程）都還沒有 2027 年。
CPI_STATIC: Dict[int, List[str]] = {
    2026: [
        "2026-01-13",
        "2026-02-13",
        "2026-03-11",
        "2026-04-10",
        "2026-05-12",
        "2026-06-10",
        "2026-07-14",
        "2026-08-12",
        "2026-09-11",
        "2026-10-14",
        "2026-11-10",
        "2026-12-10",
    ],
}
# BLS Employment Situation（非農）發布日（08:30 ET），2026-09-12 抄錄
# 來源 https://www.bls.gov/schedule/news_release/empsit.htm；
# 可抓的鏡像 https://fred.stlouisfed.org/releases/calendar?rid=50&view=year&vs=2026-01-01&ve=2026-12-31
NFP_STATIC: Dict[int, List[str]] = {
    2026: [
        "2026-01-09",
        "2026-02-11",
        "2026-03-06",
        "2026-04-03",
        "2026-05-08",
        "2026-06-05",
        "2026-07-02",
        "2026-08-07",
        "2026-09-04",
        "2026-10-02",
        "2026-11-06",
        "2026-12-04",
    ],
}

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}


def _month_number(name: str) -> Optional[int]:
    """全名或縮寫（Jan／Sept）→ 月份；認不得回 None。"""
    key = (name or "").strip().lower()
    if key in _MONTHS:
        return _MONTHS[key]
    for full, num in _MONTHS.items():
        if len(key) >= 3 and full.startswith(key):
            return num
    return None


def parse_fomc_html(html: str) -> Dict[int, List[date]]:
    """Fed 行事曆頁 → {年: [決議日]}（純函式，給測試餵固定 HTML）。"""
    out: Dict[int, List[date]] = {}
    for panel in html.split('<div class="panel panel-default">'):
        m = re.search(r"(\d{4}) FOMC Meetings", panel)
        if not m:
            continue
        year = int(m.group(1))
        pairs = re.findall(
            r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>\s*</div>\s*"
            r'<div class="fomc-meeting__date[^>]*>([^<]+)</div>',
            panel,
        )
        days: List[date] = []
        for month_raw, day_raw in pairs:
            month = _month_number(month_raw)
            nums = re.findall(r"\d+", day_raw)
            if not month or not nums:
                continue
            # 「31-Feb 1」這種跨月會議：日期字串含第二個月份名（可能是縮寫）
            tail = re.search(r"([A-Za-z]+)\s*\d+\s*\*?\s*$", day_raw)
            if tail:
                month = _month_number(tail.group(1)) or month
            try:
                days.append(date(year, month, int(nums[-1])))
            except ValueError:
                continue
        if days:
            out[year] = sorted(days)
    return out


def _static_fomc() -> List[date]:
    return sorted(date(y, m, d) for y, pairs in FOMC_STATIC.items() for (m, d) in pairs)


def fomc_decision_dates() -> List[date]:
    """快取 → 官網解析 → 靜態表。永遠回一份排序好的清單。"""
    from core.database.cache import get_cache, set_cache

    try:
        cached = get_cache(FOMC_CACHE_KEY)
    except Exception:  # noqa: BLE001
        cached = None
    if isinstance(cached, dict) and cached.get("dates"):
        return [date.fromisoformat(s) for s in cached["dates"]]
    parsed: Dict[int, List[date]] = {}
    try:
        resp = httpx.get(FOMC_URL, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True)
        resp.raise_for_status()
        parsed = parse_fomc_html(resp.text)
    except Exception as exc:  # noqa: BLE001 — 官網抓不到就用靜態表
        logger.info("[macro] fomc fetch failed: %s", type(exc).__name__)
    dates = (
        sorted(d for ds in parsed.values() for d in ds) if parsed else _static_fomc()
    )
    if parsed:
        try:
            set_cache(
                FOMC_CACHE_KEY,
                {"dates": [d.isoformat() for d in dates]},
                ttl=FOMC_CACHE_TTL_SECONDS,
            )
        except Exception:  # noqa: BLE001
            pass
    return dates


def _static_dates(table: Dict[int, List[str]]) -> List[date]:
    return sorted(date.fromisoformat(s) for ds in table.values() for s in ds)


def fred_release_dates(code: str, *, api_key: str, start: date) -> List[date]:
    """FRED release/dates（需要 key）；失敗回空清單讓呼叫端退回靜態表。"""
    release_id = FRED_RELEASE_IDS.get(code)
    if not release_id or not api_key:
        return []
    try:
        resp = httpx.get(
            FRED_RELEASE_URL,
            params={
                "release_id": release_id,
                "api_key": api_key,
                "file_type": "json",
                "include_release_dates_with_no_data": "true",
                "realtime_start": start.isoformat(),
                "realtime_end": "9999-12-31",
                "sort_order": "asc",
            },
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        rows = resp.json().get("release_dates") or []
        return sorted(
            {date.fromisoformat(str(r["date"])[:10]) for r in rows if r.get("date")}
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("[macro] fred %s failed: %s", code, type(exc).__name__)
        return []


def release_dates(code: str, *, today: date) -> List[date]:
    """CPI／非農發布日：FRED（有 key 時）→ 靜態表。"""
    key = os.getenv("FRED_SERVICE_API_KEY", "").strip()
    if key:
        live = fred_release_dates(code, api_key=key, start=today)
        if live:
            return live
    table = CPI_STATIC if code == CODE_CPI else NFP_STATIC
    return _static_dates(table)


def upcoming_macro_events(
    *, today: date, days: int = DEFAULT_HORIZON_DAYS
) -> List[Dict[str, Any]]:
    """未來 ``days`` 天的總經事件，依日期排序。每筆 {code, date}。"""
    end = today + timedelta(days=days)
    events: List[Dict[str, Any]] = []
    for code, dates in (
        (CODE_FOMC, fomc_decision_dates()),
        (CODE_CPI, release_dates(CODE_CPI, today=today)),
        (CODE_NFP, release_dates(CODE_NFP, today=today)),
    ):
        for d in dates:
            if today <= d <= end:
                events.append({"code": code, "date": d})
    return sorted(events, key=lambda e: (e["date"], e["code"]))


def schedule_known_through(*, today: date) -> Dict[str, date]:
    """各事件目前拿得到的最晚日期。

    超過這天查不到＝官方還沒公布排程（BLS 通常秋天才出下一年），不是「沒有發布」——
    給 AI 工具用，避免把資料缺口講成「1 月沒有 CPI」。
    """
    known: Dict[str, date] = {}
    for code, dates in (
        (CODE_FOMC, fomc_decision_dates()),
        (CODE_CPI, release_dates(CODE_CPI, today=today)),
        (CODE_NFP, release_dates(CODE_NFP, today=today)),
    ):
        if dates:
            known[code] = max(dates)
    return known


def coverage_gaps(known: Dict[str, date], *, horizon_end: date) -> List[str]:
    """查詢範圍超出官方已公布日期的事件代碼（排序好）。"""
    return sorted(code for code, last in known.items() if last < horizon_end)


def static_coverage_end() -> date:
    """靜態表最晚涵蓋到哪天（守衛測試用）。"""
    return min(
        max(_static_fomc()),
        max(_static_dates(CPI_STATIC)),
        max(_static_dates(NFP_STATIC)),
    )


def now_utc_date() -> date:
    return datetime.now(timezone.utc).date()
