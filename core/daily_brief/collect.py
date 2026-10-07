"""撈早報要用的資料——全部是既有來源，不新增任何資料表的讀寫以外的東西。

- 持倉：帳本 ``get_positions``＋各市場現價（沿用 alert_checker 的 ``_fetch_price``，
  只認 crypto／tw_stock／us_stock；其他市場不算漲跌）
- 花費：帳本 ``get_category_summary(entry_type="expense")`` 昨日／本月／上月同期
- 警報：alert_checker 觸發時寫的 ``price_alert`` 站內通知（過去 24 小時）
- 事件：``user_calendar_events``（提醒窗＝事件日往前 remind_days_before 天）
- 自選：每天都列（user_watchlist，持倉已列的不重複），最多抓 20 檔報價
- 不列的標的：使用者在設定頁關掉「早報」的持倉／自選（user_brief_hidden_symbols，c057）
- 月結：每月 1 日補上月交易筆數、支出總額與前三類
- 判斷結果：上次早報之後評完分的 ``judgment_scores``（PR-7）
- 市場概況：以上都沒有、早報會是空的才抓——BTC／ETH／SPY（沿用 ``_fetch_price``）＋
  下一個總經事件（``macro_calendar`` 靜態表）；新用戶第一天也收得到東西（PR-7）
"""

from __future__ import annotations

import asyncio
import logging
import time as _clock
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.daily_brief import store
from core.daily_brief.compose import BriefData, compose_brief
from core.daily_brief.schedule import BriefPrefs
from core.database.trading import WATCHLIST_MARKETS

logger = logging.getLogger(__name__)

# 抓得到報價的市場＝自選清單的市場（api/alert_checker._fetch_price，10 個）
PRICEABLE_MARKETS = set(WATCHLIST_MARKETS)
MAX_PRICE_LOOKUPS = 12
# 自選每天最多抓幾檔報價；早報依漲跌幅挑前 compose.MAX_WATCHLIST_ROWS（10）檔列出
MAX_WATCHLIST_LOOKUPS = 20
# 撈事件的窗要蓋住最長的提前提醒天數（api/routers/calendar.py 的
# remind_days_before 上限 30）——此前只撈 7 天，提前 8～30 天的提醒默默不出現。
EVENT_LOOKAHEAD_DAYS = 31
# 判斷結果：從上次早報起算，最多回看 7 天（停送一陣子再開不會一次倒一大串）
SCORE_LOOKBACK_MAX_DAYS = 7
# 市場概況：(symbol, market, 顯示名)。報價在同一個 cron 程序內共用 5 分鐘。
MARKET_OVERVIEW_SYMBOLS = (
    ("BTC", "crypto", "BTC"),
    ("ETH", "crypto", "ETH"),
    ("SPY", "us_stock", "SPY"),
)
MARKET_OVERVIEW_TTL_SECONDS = 300
MACRO_LOOKAHEAD_DAYS = 30
_overview_cache: Dict[str, Any] = {}


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def _day_bounds(day: date, tz: ZoneInfo) -> tuple[str, str]:
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day, time.max, tzinfo=tz)
    return start.isoformat(), end.isoformat()


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _prev_month_same_day(day: date) -> tuple[date, date]:
    """上月 1 日 ～ 上月同一天（天數不夠就取上月最後一天）。"""
    first_this = _month_start(day)
    last_prev = first_this - timedelta(days=1)
    first_prev = _month_start(last_prev)
    same = min(day.day, last_prev.day)
    return first_prev, first_prev.replace(day=same)


async def _fetch_price_safe(symbol: str, market: str):
    try:
        from api.alert_checker import _fetch_price

        return await _fetch_price(symbol, market)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "[daily_brief] price lookup failed %s/%s: %s",
            market,
            symbol,
            type(exc).__name__,
        )
        return None


def scores_since(prefs: BriefPrefs, now_utc: datetime) -> datetime:
    """判斷結果從哪時起算：上次早報的送出時刻（last_sent_on 的本地 send_hour）。

    評分 cron 在 00:50 UTC，台北 08:00 早報在它之前——所以前一天評完的會落在隔天那份。
    沒送過＝往回 24 小時；最多回看 SCORE_LOOKBACK_MAX_DAYS 天。
    """
    floor = now_utc - timedelta(days=SCORE_LOOKBACK_MAX_DAYS)
    last = prefs.last_sent_on
    if last is None:
        return now_utc - timedelta(hours=24)
    sent_at = datetime.combine(
        last, time(hour=prefs.send_hour), tzinfo=_zone(prefs.timezone)
    ).astimezone(timezone.utc)
    return max(sent_at, floor)


async def _market_quotes() -> List[Dict[str, Any]]:
    """BTC／ETH／SPY 的現價與漲跌；同程序 5 分鐘內共用（cron 一批人只抓一次）。"""
    cached = _overview_cache.get("quotes")
    if cached and _clock.monotonic() - _overview_cache.get("at", 0.0) < (
        MARKET_OVERVIEW_TTL_SECONDS
    ):
        return cached
    quotes = []
    for symbol, market, label in MARKET_OVERVIEW_SYMBOLS:
        res = await _fetch_price_safe(symbol, market)
        if not res:
            continue
        cur, opn = res
        pct = (float(cur) - float(opn)) / float(opn) * 100 if opn else None
        quotes.append({"label": label, "price": float(cur), "change_pct": pct})
    if quotes:  # 全部抓不到不快取，下一個人再試
        _overview_cache.update(at=_clock.monotonic(), quotes=quotes)
    return quotes


def _next_macro_event(today: date, language: str) -> Optional[Dict[str, Any]]:
    from core.i18n import t
    from core.market_events.macro_calendar import upcoming_macro_events

    events = upcoming_macro_events(today=today, days=MACRO_LOOKAHEAD_DAYS)
    if not events:
        return None
    ev = events[0]
    return {
        "date": ev["date"],
        "title": t(f"ui_messages.daily_brief.sys_macro_{ev['code']}", language),
    }


# 空早報補市場概況的新帳號期限（天）
OVERVIEW_NEW_ACCOUNT_DAYS = 14


def overview_eligible(prefs: BriefPrefs, now_utc: datetime) -> bool:
    """內容空的早報要不要補市場概況：自己設過早報偏好的人，或開帳號未滿 14 天的人。

    建立時間未知（舊資料／測試列）一律當成不是新帳號——寧可少送，不要誤推。
    """
    if prefs.prefs_user_set:
        return True
    created = prefs.account_created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return now_utc - created <= timedelta(days=OVERVIEW_NEW_ACCOUNT_DAYS)


async def market_overview(prefs: BriefPrefs, today: date) -> Optional[Dict[str, Any]]:
    quotes = await _market_quotes()
    next_event = None
    if prefs.include_macro:
        try:
            next_event = _next_macro_event(today, prefs.language)
        except Exception as exc:  # noqa: BLE001 — 靜態表壞了也不擋報價
            logger.info("[daily_brief] macro lookup skipped: %s", type(exc).__name__)
    if not quotes and not next_event:
        return None
    return {"quotes": quotes, "next_event": next_event}


def _sum_expense(
    repo, date_from: str, date_to: str
) -> tuple[float, List[Dict[str, Any]]]:
    rows = repo.get_category_summary(
        entry_type="expense", date_from=date_from, date_to=date_to
    )
    by_cat: Dict[str, float] = {}
    for r in rows:
        by_cat[str(r.get("category") or "other")] = by_cat.get(
            str(r.get("category") or "other"), 0.0
        ) + float(r.get("total") or 0)
    ordered = sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True)
    return sum(by_cat.values()), [{"category": c, "total": v} for c, v in ordered]


async def collect_brief_data(
    prefs: BriefPrefs, now_utc: Optional[datetime] = None
) -> BriefData:
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    tz = _zone(prefs.timezone)
    local_now = now_utc.astimezone(tz)
    today = local_now.date()
    yesterday = today - timedelta(days=1)

    from core.orm.trade_journal_repo import get_journal_repo

    repo = get_journal_repo(prefs.user_id)
    data = BriefData(
        language=prefs.language,
        date_local=today,
        display_name=prefs.display_name,
        base_currency=repo.base_currency,
        telegram_hint="telegram" in prefs.channels and prefs.telegram_id is not None,
    )

    # 使用者關掉「早報」的標的：持倉與自選都跳過（讀不到就當沒有，不擋早報）
    try:
        hidden = store.hidden_symbols(prefs.user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[daily_brief] hidden symbols failed user=%s: %s", prefs.user_id, exc
        )
        hidden = set()

    # ── 持倉＋現價 ────────────────────────────────────────────────
    try:
        open_positions = [
            p
            for p in repo.get_positions()
            if float(p.get("quantity") or 0) > 0
            and (p.get("market"), str(p.get("symbol") or "").upper()) not in hidden
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[daily_brief] positions failed user=%s: %s", prefs.user_id, exc)
        open_positions = []
    positions: List[Dict[str, Any]] = []
    for p in open_positions:
        change_pct = None
        current = None
        if p.get("market") in PRICEABLE_MARKETS and len(positions) < MAX_PRICE_LOOKUPS:
            res = await _fetch_price_safe(p["symbol"], p["market"])
            if res:
                current, open_price = res
                if open_price:
                    change_pct = (
                        (float(current) - float(open_price)) / float(open_price) * 100
                    )
        positions.append(
            {
                "symbol": p["symbol"],
                "market": p.get("market"),
                "quantity": float(p.get("quantity") or 0),
                "current_price": current,
                "change_pct": change_pct,
            }
        )
    positions.sort(key=lambda x: abs(x["change_pct"] or 0), reverse=True)
    data.positions = positions

    # ── Phase C：有持倉但沒設警報的標的（早報末尾建議一句，不自動建） ──
    if positions:
        try:
            from core.database.price_alerts import get_user_alerts

            alerted = {
                str(a.get("symbol") or "").upper()
                for a in (get_user_alerts(prefs.user_id) or [])
            }
            data.alert_suggestions = [
                p["symbol"]
                for p in positions
                if p.get("market") in PRICEABLE_MARKETS
                and str(p["symbol"]).upper() not in alerted
            ][:2]
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[daily_brief] alert suggestion skipped user=%s: %s",
                prefs.user_id,
                type(exc).__name__,
            )

    # ── 花費 ─────────────────────────────────────────────────────
    if prefs.include_spend:
        try:
            y_from, y_to = _day_bounds(yesterday, tz)
            _, data.spend_yesterday = _sum_expense(repo, y_from, y_to)
            m_from, _ = _day_bounds(_month_start(today), tz)
            _, t_to = _day_bounds(today, tz)
            data.spend_mtd, _ = _sum_expense(repo, m_from, t_to)
            p_first, p_same = _prev_month_same_day(today)
            pf, _ = _day_bounds(p_first, tz)
            _, ps = _day_bounds(p_same, tz)
            data.spend_prev_same_period, _ = _sum_expense(repo, pf, ps)
            if not data.spend_yesterday:
                data.spend_mtd = None
                data.spend_prev_same_period = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[daily_brief] spend failed user=%s: %s", prefs.user_id, exc)
            data.spend_yesterday = []

    # ── 警報（過去 24h 觸發的） ──────────────────────────────────
    try:
        data.alerts = store.recent_alert_notifications(
            prefs.user_id, now_utc - timedelta(hours=24)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[daily_brief] alerts failed user=%s: %s", prefs.user_id, exc)

    # ── 事件（提醒窗） ───────────────────────────────────────────
    try:
        rows = store.list_events_between(
            prefs.user_id, today, today + timedelta(days=EVENT_LOOKAHEAD_DAYS)
        )
        events = []
        for e in rows:
            d = e.get("event_date")
            if isinstance(d, datetime):
                d = d.date()
            # 0＝當天才提醒，不能用 `or 1` 吃掉（那會提前一天出現）
            raw_lead = e.get("remind_days_before")
            lead = 1 if raw_lead is None else int(raw_lead)
            if isinstance(d, date) and (d - timedelta(days=lead)) <= today <= d:
                events.append({**e, "event_date": d})
        data.events = events
    except Exception as exc:  # noqa: BLE001
        logger.warning("[daily_brief] events failed user=%s: %s", prefs.user_id, exc)

    # ── 判斷結果（上次早報之後評完分的） ─────────────────────────
    try:
        from core.scorecard import store as score_store

        data.scored_calls = score_store.scored_since(
            prefs.user_id, scores_since(prefs, now_utc)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[daily_brief] scores failed user=%s: %s", prefs.user_id, exc)
        data.scored_calls = []

    # ── 自選（使用者自己挑的標的；持倉已列的不重複） ─────────────
    # 2026-09-27：每天都列（原本只在沒持倉時當備用、而且一律當加密貨幣抓價）。
    # 抓不到報價也列出來（價格顯示「-」），使用者才知道那一檔還在追蹤。
    try:
        from core.database.trading import get_watchlist

        held = {(p.get("market"), str(p["symbol"]).upper()) for p in positions}
        watch = []
        for item in get_watchlist(prefs.user_id) or []:
            if len(watch) >= MAX_WATCHLIST_LOOKUPS:
                break
            if (item["market"], item["symbol"].upper()) in held | hidden:
                continue
            cur = pct = None
            res = await _fetch_price_safe(item["symbol"], item["market"])
            if res:
                cur, opn = res
                pct = (float(cur) - float(opn)) / float(opn) * 100 if opn else None
            watch.append(
                {
                    "symbol": item["symbol"],
                    "market": item["market"],
                    "price": cur,
                    "change_pct": pct,
                }
            )
        # 動得最多的排前面（跟持倉一樣）；抓不到報價的排最後
        watch.sort(key=lambda w: -abs(w["change_pct"]) if w["change_pct"] is not None else 1)
        data.watchlist = watch
    except Exception as exc:  # noqa: BLE001
        logger.warning("[daily_brief] watchlist failed user=%s: %s", prefs.user_id, exc)

    # ── 月結（每月 1 日） ────────────────────────────────────────
    if today.day == 1:
        try:
            last_prev = today - timedelta(days=1)
            first_prev = _month_start(last_prev)
            pf, _ = _day_bounds(first_prev, tz)
            _, pl = _day_bounds(last_prev, tz)
            trades = repo.list_trades(
                entry_type="trade", date_from=pf, date_to=pl, limit=10000
            )
            expense_total, cats = _sum_expense(repo, pf, pl)
            before_last = first_prev - timedelta(days=1)
            bf, _ = _day_bounds(_month_start(before_last), tz)
            _, bl = _day_bounds(before_last, tz)
            expense_prev_total, _ = _sum_expense(repo, bf, bl)
            data.monthly = {
                "trades": len(trades),
                "expense_total": expense_total,
                "expense_prev_total": expense_prev_total,
                "top_expense": [(c["category"], c["total"]) for c in cats[:3]],
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[daily_brief] monthly failed user=%s: %s", prefs.user_id, exc
            )

    # ── 會空的話補市場概況（新用戶第一天也要收得到） ──────────────
    # 只給新帳號與自己開過早報的人：很久以前綁 Telegram（早報預設開）、從沒記帳的人
    # 維持「空就不送」，不因這條補送突然開始每天收到訊息（首次主動推播要 DANNY 拍板）。
    if compose_brief(data) is None and overview_eligible(prefs, now_utc):
        try:
            data.market_overview = await market_overview(prefs, today)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[daily_brief] market overview failed user=%s: %s", prefs.user_id, exc
            )

    return data
