"""系統事件同步（Phase B＋2026-09-12 擴充）：依使用者持倉自動產生行事曆事件，早報前一天提醒。

- 美股：持倉／自選裡的 us_stock 標的 → ``next_earnings_date``（既有 provider）
- 港股／日股／韓股：yfinance ``Ticker.calendar["Earnings Date"]``（0700.HK／7203.T／005930.KS
  實測有日期）；A 股 yfinance 沒有 → 法定定期報告截止（4/30、8/31、10/31）用規則
- 台股：持倉有 tw_stock → 月營收公布截止（每月 10 日）、季報申報截止
  （3/31、5/15、8/14、11/14）——法定日期，用規則不用抓
- 自選清單（``watchlist``，只有 symbol 沒有 market）：依後綴推市場（.HK／.T／.KS／.SS…），
  純字母＝美股、四位數字＝台股
- 代幣解鎖：持倉有 crypto → DefiLlama 解鎖索引（``core.market_events.token_unlocks``），
  未來 30 天、單日 ≥ 流通量 0.1% 的才提醒
- 總經：``user_brief_prefs.include_macro``（預設開）→ FOMC 決議日、美國 CPI、非農
  （``core.market_events.macro_calendar``），未來 45 天

事件用 ``dedupe_key`` 去重（``system:<kind>:<symbol>:<date>``），每天重跑不會重複。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.daily_brief import store
from core.i18n import t as _t

logger = logging.getLogger(__name__)

EARNINGS_HORIZON_DAYS = 90
TW_QUARTERLY_DEADLINES: tuple[tuple[int, int], ...] = (
    (3, 31),
    (5, 15),
    (8, 14),
    (11, 14),
)
TW_MONTHLY_REVENUE_DAY = 10
# 中國證監會定期報告披露截止：年報＋一季報 4/30、半年報 8/31、三季報 10/31
CN_REPORT_DEADLINES: tuple[tuple[int, int], ...] = ((4, 30), (8, 31), (10, 31))
# 有財報日資料源的市場 → yfinance 後綴（美股不用後綴，走既有 provider）
EARNINGS_SUFFIX: Dict[str, str] = {
    "us_stock": "",
    "hk_stock": ".HK",
    "jp_stock": ".T",
    "kr_stock": ".KS",
}

EarningsLookup = Callable[[str], Optional[date]]
UnlockIndex = Dict[str, Dict[str, Any]]
MacroEvents = List[Dict[str, Any]]
MACRO_HORIZON_DAYS = 45


def tw_monthly_revenue_date(today: date) -> date:
    """本月 10 日還沒過就是本月，過了就是下月 10 日。"""
    if today.day <= TW_MONTHLY_REVENUE_DAY:
        return today.replace(day=TW_MONTHLY_REVENUE_DAY)
    first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first_next.replace(day=TW_MONTHLY_REVENUE_DAY)


def next_tw_quarterly_deadline(today: date) -> date:
    for month, day in TW_QUARTERLY_DEADLINES:
        candidate = date(today.year, month, day)
        if candidate >= today:
            return candidate
    return date(
        today.year + 1, TW_QUARTERLY_DEADLINES[0][0], TW_QUARTERLY_DEADLINES[0][1]
    )


def next_cn_report_deadline(today: date) -> date:
    for month, day in CN_REPORT_DEADLINES:
        candidate = date(today.year, month, day)
        if candidate >= today:
            return candidate
    return date(today.year + 1, CN_REPORT_DEADLINES[0][0], CN_REPORT_DEADLINES[0][1])


def earnings_symbol(symbol: str, market: str) -> Optional[str]:
    """持倉 symbol → 財報查詢代號（美股原樣；港日韓補後綴；已帶後綴不重複）。"""
    if market not in EARNINGS_SUFFIX:
        return None
    s = str(symbol or "").strip().upper()
    if not s:
        return None
    from core.markets import strip_exchange_suffix

    if market == "hk_stock":
        from core.providers.base_provider import normalize_symbol

        return normalize_symbol(strip_exchange_suffix(s), "hk")  # 700 → 0700.HK

    suffix = EARNINGS_SUFFIX[market]
    if suffix and not s.endswith(suffix):
        s = strip_exchange_suffix(s) + suffix
    elif not suffix:
        s = strip_exchange_suffix(s)  # 美股帶了 .TW 之類的錯後綴就拿掉
    return s


def watchlist_positions(items: Iterable) -> List[dict]:
    """自選清單 → 與持倉同形的 {symbol, market}。

    user_watchlist（c056）的項目自帶 market，直接用；純代號字串則由代號推市場（去交易所後綴）。
    """
    from core.markets import infer_market, strip_exchange_suffix

    out: List[dict] = []
    for raw in items or []:
        if isinstance(raw, dict):
            if raw.get("market") and raw.get("symbol"):
                out.append(
                    {"symbol": raw["symbol"], "market": raw["market"], "quantity": 0, "watch": True}
                )
            continue
        market = infer_market(raw)
        if not market:
            continue
        out.append({"symbol": strip_exchange_suffix(raw), "market": market, "quantity": 0, "watch": True})
    return out


def plan_unlock_events(
    *,
    positions: Iterable[dict],
    today: date,
    language: str,
    unlock_index: UnlockIndex,
) -> List[dict]:
    """純函式：crypto 持倉 → 解鎖事件（同一 symbol 同一天一筆）。"""
    from core.market_events.token_unlocks import (
        CALENDAR_HORIZON_DAYS,
        CALENDAR_MIN_PCT_OF_CIRC,
        format_amount,
        upcoming_unlocks,
    )

    events: List[dict] = []
    symbols = sorted(
        {
            str(p["symbol"]).upper()
            for p in positions
            if p.get("market") == "crypto" and p.get("symbol")
        }
    )
    for sym in symbols:
        for ev in upcoming_unlocks(
            sym, today=today, days=CALENDAR_HORIZON_DAYS, index=unlock_index
        ):
            pct = ev.get("pct_of_circ")
            if pct is None or pct < CALENDAR_MIN_PCT_OF_CIRC:
                continue
            d = ev["date"]
            events.append(
                {
                    "event_date": d,
                    "title": _t(
                        "ui_messages.daily_brief.sys_unlock",
                        language,
                        symbol=sym,
                        amount=format_amount(ev["amount"], language),
                        pct=f"{pct:.2f}",
                    ),
                    "kind": "unlock",
                    "symbol": sym,
                    "market": "crypto",
                    "dedupe_key": f"system:unlock:{sym}:{d.isoformat()}",
                }
            )
    return events


def plan_macro_events(*, today: date, language: str, macro_events: MacroEvents) -> List[dict]:
    """純函式：總經事件清單 → 行事曆事件（全平台同 dedupe_key，各 user 一份）。"""
    events: List[dict] = []
    for ev in macro_events:
        d = ev["date"]
        code = ev["code"]
        events.append(
            {
                "event_date": d,
                "title": _t(f"ui_messages.daily_brief.sys_macro_{code}", language),
                "kind": "macro",
                "symbol": None,
                "market": "us_macro",
                "dedupe_key": f"system:macro:{code}:{d.isoformat()}",
            }
        )
    return events


def plan_system_events(
    *,
    positions: Iterable[dict],
    today: date,
    language: str,
    earnings_lookup: EarningsLookup,
    unlock_index: Optional[UnlockIndex] = None,
    macro_events: Optional[MacroEvents] = None,
) -> List[dict]:
    """純函式：持倉 → 要 upsert 的系統事件清單。

    ``unlock_index``／``macro_events`` 傳 None＝不產生那一類（呼叫端依偏好與資料源決定）。
    """
    positions = list(positions)
    events: List[dict] = []
    # 財報日：美股（provider）＋港日韓（yfinance 代號帶後綴）；同一代號只查一次
    earnings_targets: Dict[str, tuple[str, str]] = {}
    for p in positions:
        q = earnings_symbol(p.get("symbol"), str(p.get("market") or ""))
        if q and q not in earnings_targets:
            earnings_targets[q] = (str(p.get("symbol")).upper(), str(p["market"]))
    tw_syms = [p for p in positions if p.get("market") == "tw_stock"]
    cn_syms = [p for p in positions if p.get("market") == "cn_stock"]

    for query in sorted(earnings_targets):
        sym, market = earnings_targets[query]
        try:
            d = earnings_lookup(query)
        except Exception as exc:  # noqa: BLE001 — 單一標的查不到不影響其他
            logger.info(
                "[calendar_sync] earnings lookup failed %s: %s", query, type(exc).__name__
            )
            d = None
        if not d or not (today <= d <= today + timedelta(days=EARNINGS_HORIZON_DAYS)):
            continue
        events.append(
            {
                "event_date": d,
                "title": _t(
                    "ui_messages.daily_brief.sys_earnings", language, symbol=query
                ),
                "kind": "earnings",
                "symbol": sym,
                "market": market,
                "dedupe_key": f"system:earnings:{query}:{d.isoformat()}",
            }
        )

    if cn_syms:
        cn = next_cn_report_deadline(today)
        events.append(
            {
                "event_date": cn,
                "title": _t("ui_messages.daily_brief.sys_cn_report", language),
                "kind": "report",
                "symbol": None,
                "market": "cn_stock",
                "dedupe_key": f"system:report:cn:{cn.isoformat()}",
            }
        )

    if tw_syms:
        rev = tw_monthly_revenue_date(today)
        events.append(
            {
                "event_date": rev,
                "title": _t("ui_messages.daily_brief.sys_tw_revenue", language),
                "kind": "revenue",
                "symbol": None,
                "market": "tw_stock",
                "dedupe_key": f"system:revenue:tw:{rev.isoformat()}",
            }
        )
        q = next_tw_quarterly_deadline(today)
        events.append(
            {
                "event_date": q,
                "title": _t("ui_messages.daily_brief.sys_tw_quarterly", language),
                "kind": "report",
                "symbol": None,
                "market": "tw_stock",
                "dedupe_key": f"system:report:tw:{q.isoformat()}",
            }
        )
    if unlock_index:
        events.extend(
            plan_unlock_events(
                positions=positions,
                today=today,
                language=language,
                unlock_index=unlock_index,
            )
        )
    if macro_events:
        events.extend(
            plan_macro_events(today=today, language=language, macro_events=macro_events)
        )
    return events


def yfinance_earnings_date(query: str, *, today: Optional[date] = None) -> Optional[date]:
    """yfinance ``Ticker.calendar["Earnings Date"]`` 裡第一個 ≥ 今天的日期（港日韓用）。"""
    import yfinance as yf

    today = today or datetime.now(timezone.utc).date()
    cal = yf.Ticker(query).calendar
    dates = (cal or {}).get("Earnings Date") if isinstance(cal, dict) else None
    for item in dates or []:
        d = item.date() if hasattr(item, "date") and not isinstance(item, date) else item
        if isinstance(d, date) and d >= today:
            return d
    return None


def default_earnings_lookup(symbol: str) -> Optional[date]:
    """帶交易所後綴（0700.HK／7203.T／005930.KS）→ yfinance；純美股代號 → 既有 provider。"""
    if "." in symbol:
        return yfinance_earnings_date(symbol)
    from core.tools.us_stock_tools import _us

    info = _us().get_earnings(symbol) or {}
    raw = info.get("next_earnings_date")
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def default_unlock_index() -> UnlockIndex:
    from core.market_events.token_unlocks import get_unlock_index

    return get_unlock_index()


def default_macro_events(today: date) -> MacroEvents:
    from core.market_events.macro_calendar import upcoming_macro_events

    return upcoming_macro_events(today=today, days=MACRO_HORIZON_DAYS)


def sync_user_events(
    user_id: str,
    today: date,
    language: str = "zh-TW",
    *,
    include_macro: bool = True,
    earnings_lookup: EarningsLookup = default_earnings_lookup,
    unlock_index: Optional[UnlockIndex] = None,
    macro_events: Optional[MacroEvents] = None,
) -> int:
    """撈持倉 → 規劃 → upsert。回傳寫入（含更新）的事件數。

    ``unlock_index``／``macro_events`` 讓 cron 一批人共用同一份（各抓一次）；沒傳就自己抓。
    """
    from core.orm.trade_journal_repo import get_journal_repo

    repo = get_journal_repo(user_id)
    positions = [p for p in repo.get_positions() if float(p.get("quantity") or 0) > 0]
    try:
        from core.database.trading import get_watchlist

        positions += watchlist_positions(get_watchlist(user_id))
    except Exception as exc:  # noqa: BLE001 — 自選讀不到只是少幾個事件
        logger.info("[calendar_sync] watchlist failed user=%s: %s", user_id, type(exc).__name__)
    has_crypto = any(p.get("market") == "crypto" for p in positions)
    if unlock_index is None and has_crypto:
        unlock_index = default_unlock_index()
    if include_macro and macro_events is None:
        macro_events = default_macro_events(today)
    planned = plan_system_events(
        positions=positions,
        today=today,
        language=language,
        earnings_lookup=earnings_lookup,
        unlock_index=unlock_index if has_crypto else None,
        macro_events=macro_events if include_macro else None,
    )
    written = 0
    for ev in planned:
        try:
            store.add_event(
                user_id,
                event_date=ev["event_date"],
                title=ev["title"],
                kind=ev["kind"],
                symbol=ev.get("symbol"),
                market=ev.get("market"),
                source="system",
                remind_days_before=1,
                dedupe_key=ev["dedupe_key"],
            )
            written += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[calendar_sync] upsert failed user=%s key=%s: %s",
                user_id,
                ev["dedupe_key"],
                exc,
            )
    return written
