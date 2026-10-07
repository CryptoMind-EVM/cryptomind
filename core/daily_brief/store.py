"""早報偏好、事件日曆、警報觸發紀錄的資料存取（同步 psycopg；API 端用 run_sync 包）。

跟 cron 腳本共用同一套函式，兩條路徑行為才一致。
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from core.database.base import DatabaseBase

logger = logging.getLogger(__name__)

_CANDIDATE_SQL = """
    SELECT u.user_id, u.language, u.display_name, u.membership_tier,
           u.created_at AS account_created_at,
           b.telegram_id,
           p.user_id AS prefs_user_id, p.enabled, p.send_hour, p.timezone,
           p.channels, p.include_spend, p.include_macro, p.last_sent_on,
           p.user_set
    FROM users u
    LEFT JOIN telegram_bindings b ON b.user_id = u.user_id
    LEFT JOIN user_brief_prefs p ON p.user_id = u.user_id
    WHERE u.is_active = TRUE
"""


def list_candidates() -> List[Dict[str, Any]]:
    """所有可能收早報的人：綁了 Telegram 的、或自己設過偏好的。"""
    return DatabaseBase.query_all(
        _CANDIDATE_SQL
        + "      AND (b.telegram_id IS NOT NULL OR p.user_id IS NOT NULL)"
    )


def get_prefs_row(user_id: str) -> Optional[Dict[str, Any]]:
    return DatabaseBase.query_one(
        _CANDIDATE_SQL + "      AND u.user_id = %s", (user_id,)
    )


def upsert_prefs(
    user_id: str,
    *,
    enabled: bool,
    send_hour: int,
    timezone: str,
    channels: List[str],
    include_spend: bool,
    include_macro: bool = True,
) -> None:
    DatabaseBase.execute(
        """
        INSERT INTO user_brief_prefs
            (user_id, enabled, send_hour, timezone, channels, include_spend,
             include_macro, user_set, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            enabled = EXCLUDED.enabled,
            send_hour = EXCLUDED.send_hour,
            timezone = EXCLUDED.timezone,
            channels = EXCLUDED.channels,
            include_spend = EXCLUDED.include_spend,
            include_macro = EXCLUDED.include_macro,
            user_set = TRUE,
            updated_at = NOW()
        """,
        (
            user_id,
            bool(enabled),
            int(send_hour),
            timezone,
            list(channels),
            bool(include_spend),
            bool(include_macro),
        ),
    )


def set_enabled(user_id: str, enabled: bool, *, timezone: Optional[str] = None) -> None:
    """只切開關（bot /brief on|off）。沒列就用預設建一列。

    原本是系統記帳自動建的列（user_set=FALSE）：頻道是 DB 預設、少了 baseapp，
    這時要從程式預設開始，不然一設開關就把缺的頻道固定下來（c063）。
    """
    from core.daily_brief.schedule import DEFAULT_CHANNELS

    DatabaseBase.execute(
        """
        INSERT INTO user_brief_prefs (user_id, enabled, timezone, channels, user_set, updated_at)
        VALUES (%s, %s, COALESCE(%s, 'Asia/Taipei'), %s, TRUE, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            enabled = EXCLUDED.enabled,
            channels = CASE WHEN user_brief_prefs.user_set
                            THEN user_brief_prefs.channels ELSE EXCLUDED.channels END,
            user_set = TRUE,
            updated_at = NOW()
        """,
        (user_id, bool(enabled), timezone, list(DEFAULT_CHANNELS)),
    )


def mark_sent(user_id: str, on_date: date, *, timezone: Optional[str] = None) -> None:
    """記今天送過了（冪等鍵）。沒偏好列的人在這裡自動建一列，user_set 維持 FALSE：
    這列只是記帳，不算使用者設過偏好（c063）。"""
    DatabaseBase.execute(
        """
        INSERT INTO user_brief_prefs (user_id, timezone, last_sent_on, updated_at)
        VALUES (%s, COALESCE(%s, 'Asia/Taipei'), %s, NOW())
        ON CONFLICT (user_id) DO UPDATE SET last_sent_on = EXCLUDED.last_sent_on, updated_at = NOW()
        """,
        (user_id, timezone, on_date),
    )


def claim_day(user_id: str, on_date: date, *, timezone: Optional[str] = None) -> bool:
    """原子地搶下「這個人這一天的早報」：搶到回 True，別人已經搶走（或已送過）回 False。

    2026-09-29：9/27 有人收到兩份。cron 的檔案鎖只管同一個容器，部署交接時新舊容器重疊、
    或手動 --force 時兩邊都會送；而且 last_sent_on 是送完才記，中間隔著 LLM 好幾分鐘。
    產生內容前先搶：同時進來的第二個會等第一個的列鎖，放開後條件已不成立 → 影響 0 列。
    條件用 IS DISTINCT FROM，跟 schedule.is_due 的「!= 今天」一致（改時區往回撥也不會卡住）。
    沒偏好列的人在這裡建一列（同 mark_sent，user_set 維持 FALSE：只是記帳，不算設過偏好）。
    """
    return (
        DatabaseBase.execute(
            """
            INSERT INTO user_brief_prefs (user_id, timezone, last_sent_on, updated_at)
            VALUES (%s, COALESCE(%s, 'Asia/Taipei'), %s, NOW())
            ON CONFLICT (user_id) DO UPDATE
                SET last_sent_on = EXCLUDED.last_sent_on, updated_at = NOW()
                WHERE user_brief_prefs.last_sent_on IS DISTINCT FROM EXCLUDED.last_sent_on
            """,
            (user_id, timezone, on_date),
        )
        == 1
    )


def release_day(user_id: str, on_date: date, previous: Optional[date]) -> None:
    """搶到了但一則都沒送出去（產生內容就出錯）：還回去，讓同一小時內的下一輪還能送。
    只在它仍是這一天時才還——已經被記成別天就不動。"""
    DatabaseBase.execute(
        """
        UPDATE user_brief_prefs SET last_sent_on = %s, updated_at = NOW()
        WHERE user_id = %s AND last_sent_on = %s
        """,
        (previous, user_id, on_date),
    )


# ── 早報不列的標的（c057）：持倉與自選都適用；代號一律大寫 ─────────────────
MAX_HIDDEN_SYMBOLS = 200


def hidden_symbols(user_id: str) -> Set[Tuple[str, str]]:
    """{(market, SYMBOL)}：早報不要列的標的。"""
    rows = DatabaseBase.query_all(
        "SELECT market, symbol FROM user_brief_hidden_symbols WHERE user_id = %s",
        (user_id,),
    )
    return {(r["market"], str(r["symbol"]).upper()) for r in rows or []}


def set_symbol_hidden(user_id: str, market: str, symbol: str, hidden: bool) -> bool:
    """設定某一檔要不要出現在早報。超過上限回 False（不寫）。"""
    symbol = symbol.upper()
    if not hidden:
        DatabaseBase.execute(
            "DELETE FROM user_brief_hidden_symbols "
            "WHERE user_id = %s AND market = %s AND symbol = %s",
            (user_id, market, symbol),
        )
        return True
    row = DatabaseBase.query_one(
        "SELECT COUNT(*) AS n FROM user_brief_hidden_symbols WHERE user_id = %s",
        (user_id,),
    )
    if row and int(row["n"]) >= MAX_HIDDEN_SYMBOLS:
        return False
    DatabaseBase.execute(
        "INSERT INTO user_brief_hidden_symbols (user_id, market, symbol) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (user_id, market, symbol),
    )
    return True


def recent_alert_notifications(user_id: str, since: datetime) -> List[Dict[str, Any]]:
    """過去 24 小時觸發的價格警報——alert_checker 觸發時寫的 in-app 通知。"""
    return DatabaseBase.query_all(
        """
        SELECT title, body, created_at
        FROM notifications
        WHERE user_id = %s AND type = 'price_alert' AND created_at >= %s
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (user_id, since),
    )


# 重複事件只存一列（event_date＝錨點日），讀取時才在區間內展開。
RECURRENCES = ("none", "weekly", "monthly", "yearly")

_EVENT_COLUMNS = (
    "id, event_date, title, kind, symbol, market, source, remind_days_before, note, "
    "recurrence, recurrence_until"
)


class DuplicateEventError(Exception):
    """改完的日期＋標題跟另一筆自訂事件撞 dedupe_key。"""


def _as_date(value: Any) -> Any:
    return value.date() if isinstance(value, datetime) else value


def _nth_occurrence(anchor: date, recurrence: str, n: int) -> date:
    if recurrence == "weekly":
        return anchor + timedelta(weeks=n)
    if recurrence == "monthly":
        months = anchor.month - 1 + n
        year, month = anchor.year + months // 12, months % 12 + 1
    else:  # yearly
        year, month = anchor.year + n, anchor.month
    # 31 號、2/29 這種遇到短月份就落在月底
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def expand_occurrences(
    row: Dict[str, Any], start: date, end: date
) -> List[Dict[str, Any]]:
    """一列事件 → 區間 [start, end] 內的每一次（純函式，不改傳入的 row）。"""
    anchor = _as_date(row.get("event_date"))
    recurrence = row.get("recurrence") or "none"
    if not isinstance(anchor, date):
        return []
    if recurrence not in RECURRENCES or recurrence == "none":
        return [dict(row)] if start <= anchor <= end else []
    until = _as_date(row.get("recurrence_until"))
    last = min(end, until) if isinstance(until, date) else end
    if recurrence == "weekly":
        n = max(0, -(-(start - anchor).days // 7))
    elif recurrence == "monthly":
        n = max(0, (start.year - anchor.year) * 12 + start.month - anchor.month - 1)
    else:
        n = max(0, start.year - anchor.year - 1)
    out: List[Dict[str, Any]] = []
    while True:
        d = _nth_occurrence(anchor, recurrence, n)
        if d > last:
            break
        if d >= start:
            out.append({**row, "event_date": d, "series_start": anchor})
        n += 1
    return out


def list_events_between(user_id: str, start: date, end: date) -> List[Dict[str, Any]]:
    rows = DatabaseBase.query_all(
        f"""
        SELECT {_EVENT_COLUMNS}
        FROM user_calendar_events
        WHERE user_id = %s AND (
            (recurrence = 'none' AND event_date BETWEEN %s AND %s)
            OR (recurrence <> 'none' AND event_date <= %s
                AND (recurrence_until IS NULL OR recurrence_until >= %s))
        )
        ORDER BY event_date ASC, id ASC
        """,
        (user_id, start, end, end, start),
    )
    events = [occ for r in rows for occ in expand_occurrences(r, start, end)]
    return sorted(events, key=lambda e: (e["event_date"], e.get("id") or 0))


def add_event(
    user_id: str,
    *,
    event_date: date,
    title: str,
    kind: str = "custom",
    symbol: Optional[str] = None,
    market: Optional[str] = None,
    source: str = "user",
    remind_days_before: int = 1,
    note: str = "",
    dedupe_key: Optional[str] = None,
    recurrence: str = "none",
    recurrence_until: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    # INSERT … RETURNING 要走 transaction()（會 commit）：query_one 的連線只 close 不
    # commit，回傳 id 看起來成功、資料其實 rollback（2026-09-12 月曆實測抓到：
    # 系統事件與自訂提醒從 Phase B 上線起都沒存進去；同型事故見 trade_journal_repo）。
    from core.database.base import transaction

    with transaction() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO user_calendar_events
                    (user_id, event_date, title, kind, symbol, market, source, remind_days_before, note,
                     dedupe_key, recurrence, recurrence_until)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, dedupe_key) DO UPDATE SET
                    event_date = EXCLUDED.event_date, title = EXCLUDED.title, note = EXCLUDED.note,
                    recurrence = EXCLUDED.recurrence, recurrence_until = EXCLUDED.recurrence_until
                RETURNING {cols}
                """.format(cols=_EVENT_COLUMNS),
                (
                    user_id,
                    event_date,
                    title.strip()[:120],
                    kind,
                    symbol,
                    market,
                    source,
                    int(remind_days_before),
                    (note or "")[:500],
                    dedupe_key,
                    recurrence,
                    recurrence_until,
                ),
            )
            row = c.fetchone()
            if row is None:
                return None
            return {d.name: v for d, v in zip(c.description, row)}


def update_event(
    user_id: str,
    event_id: int,
    *,
    event_date: date,
    title: str,
    remind_days_before: int,
    note: str,
    recurrence: str,
    recurrence_until: Optional[date],
    dedupe_key: str,
) -> Optional[Dict[str, Any]]:
    """改一筆自訂事件（重複事件＝整個系列一起改）。找不到或不是自訂的回 None。

    系統事件不給改：每天同步會用 dedupe_key 把日期／標題蓋回去。
    """
    import psycopg2

    from core.database.base import transaction

    try:
        with transaction() as conn:
            with conn.cursor() as c:
                c.execute(
                    f"""
                    UPDATE user_calendar_events SET
                        event_date = %s, title = %s, remind_days_before = %s, note = %s,
                        recurrence = %s, recurrence_until = %s, dedupe_key = %s
                    WHERE user_id = %s AND id = %s AND source = 'user'
                    RETURNING {_EVENT_COLUMNS}
                    """,
                    (
                        event_date,
                        title.strip()[:120],
                        int(remind_days_before),
                        (note or "")[:500],
                        recurrence,
                        recurrence_until,
                        dedupe_key,
                        user_id,
                        int(event_id),
                    ),
                )
                row = c.fetchone()
                if row is None:
                    return None
                return {d.name: v for d, v in zip(c.description, row)}
    except psycopg2.errors.UniqueViolation as exc:
        raise DuplicateEventError() from exc


def delete_event(user_id: str, event_id: int) -> bool:
    return (
        DatabaseBase.execute(
            "DELETE FROM user_calendar_events WHERE user_id = %s AND id = %s",
            (user_id, int(event_id)),
        )
        > 0
    )


def admin_stats(days: int = 14) -> Dict[str, Any]:
    """admin 面板：早報送出量（站內通知計）、開關分佈、行事曆事件數。"""
    sends = DatabaseBase.query_all(
        """
        SELECT DATE(created_at) AS day, COUNT(*) AS n
        FROM notifications
        WHERE type = 'daily_brief' AND created_at >= NOW() - (%s || ' days')::interval
        GROUP BY DATE(created_at)
        ORDER BY day ASC
        """,
        (str(int(days)),),
    )
    prefs = (
        DatabaseBase.query_one(
            """
        SELECT
            COUNT(*) FILTER (WHERE enabled) AS enabled,
            COUNT(*) FILTER (WHERE NOT enabled) AS disabled,
            COUNT(*) FILTER (WHERE last_sent_on = CURRENT_DATE) AS sent_today
        FROM user_brief_prefs
        """
        )
        or {}
    )
    bound = (
        DatabaseBase.query_one(
            "SELECT COUNT(DISTINCT user_id) AS n FROM telegram_bindings"
        )
        or {}
    )
    events = (
        DatabaseBase.query_one(
            """
        SELECT
            COUNT(*) FILTER (WHERE source = 'user') AS user_events,
            COUNT(*) FILTER (WHERE source = 'system') AS system_events
        FROM user_calendar_events
        WHERE event_date >= CURRENT_DATE
        """
        )
        or {}
    )
    return {
        "days": int(days),
        "sends_by_day": [
            {"day": str(r.get("day")), "n": int(r.get("n") or 0)} for r in sends
        ],
        "sends_total": sum(int(r.get("n") or 0) for r in sends),
        "prefs_enabled": int(prefs.get("enabled") or 0),
        "prefs_disabled": int(prefs.get("disabled") or 0),
        "sent_today": int(prefs.get("sent_today") or 0),
        "telegram_bound": int(bound.get("n") or 0),
        "calendar_user_events": int(events.get("user_events") or 0),
        "calendar_system_events": int(events.get("system_events") or 0),
    }
