"""行事曆工具（Phase B）：聊天裡說「9/15 提醒我房租」就進 ``user_calendar_events``，
早報前一天與當天列出。低風險、可在帳本分頁刪除，不走 consent。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

MAX_DAYS_AHEAD = 366


def _get_current_user_id():
    from core.tools.key_resolver import get_current_user_id

    return get_current_user_id()


def parse_event_date(raw: str, today: date | None = None) -> date | None:
    """接受 YYYY-MM-DD 或 MM-DD／M/D（沒給年就取最近的未來日期）。"""
    today = today or date.today()
    text = (raw or "").strip().replace("/", "-").replace(".", "-")
    if not text:
        return None
    parts = [p for p in text.split("-") if p]
    try:
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        if len(parts) == 2:
            month, day = int(parts[0]), int(parts[1])
            candidate = date(today.year, month, day)
            if candidate < today:
                candidate = date(today.year + 1, month, day)
            return candidate
    except ValueError:
        return None
    return None


RECURRENCES = ("none", "weekly", "monthly", "yearly")


@tool("add_calendar_event")
def add_calendar_event(
    event_date: str,
    title: str,
    remind_days_before: int = 1,
    note: str = "",
    recurrence: str = "none",
) -> str:
    """把一個日期事件加進使用者的行事曆，早報會在前一天與當天提醒。
    使用者說「X 月 Y 日提醒我…」「幫我記某檔股票的財報日」時用。
    event_date 用 YYYY-MM-DD（或 MM-DD，會取最近的未來日期）；title 一句話；
    remind_days_before 預設 1（前一天）。
    recurrence：none（預設）／weekly／monthly／yearly——「每月 5 號繳卡費」用 monthly，
    event_date 填下一次的日期。"""
    user_id = _get_current_user_id()
    if not user_id:
        return "Error: login required to use the calendar"
    recurrence = (recurrence or "none").strip().lower()
    if recurrence not in RECURRENCES:
        return f"Error: recurrence must be one of {', '.join(RECURRENCES)}"
    when = parse_event_date(event_date)
    if not when:
        return f"Error: cannot parse date '{event_date}' (use YYYY-MM-DD)"
    today = date.today()
    if when < today - timedelta(days=1) or when > today + timedelta(
        days=MAX_DAYS_AHEAD
    ):
        return f"Error: date {when.isoformat()} is out of range (today..+{MAX_DAYS_AHEAD} days)"
    clean_title = " ".join((title or "").split()).strip()
    if not clean_title:
        return "Error: title is required"
    try:
        lead = max(0, min(30, int(remind_days_before)))
    except (TypeError, ValueError):
        lead = 1
    from core.daily_brief import store

    try:
        row = store.add_event(
            user_id,
            event_date=when,
            title=clean_title,
            kind="custom",
            source="user",
            remind_days_before=lead,
            note=note or "",
            dedupe_key=f"user:{when.isoformat()}:{clean_title.lower()[:60]}",
            recurrence=recurrence,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calendar_tool] add failed user=%s: %s", user_id, exc)
        return "Error: could not save the event, please try again"
    return json.dumps(
        {
            "ok": True,
            "id": row.get("id") if row else None,
            "event_date": when.isoformat(),
            "title": clean_title,
            "remind_days_before": lead,
            "recurrence": recurrence,
            "note": f"Saved. The daily brief will remind {lead} day(s) before and on the day.",
        },
        ensure_ascii=False,
    )


@tool("list_calendar_events")
def list_calendar_events(days: int = 14) -> str:
    """列出使用者接下來 N 天（預設 14）的行事曆事件：系統自動加的（財報、月營收）
    與使用者自己標記的。使用者問「最近有什麼要注意的日子」時用。"""
    user_id = _get_current_user_id()
    if not user_id:
        return "Error: login required to use the calendar"
    try:
        span = max(1, min(120, int(days)))
    except (TypeError, ValueError):
        span = 14
    from core.daily_brief import store

    today = date.today()
    try:
        rows = store.list_events_between(user_id, today, today + timedelta(days=span))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calendar_tool] list failed user=%s: %s", user_id, exc)
        return "Error: could not load events"
    items = []
    for r in rows:
        d = r.get("event_date")
        if isinstance(d, datetime):
            d = d.date()
        items.append(
            {
                "id": r.get("id"),
                "date": d.isoformat() if isinstance(d, date) else str(d),
                "title": r.get("title"),
                "source": r.get("source"),
                "kind": r.get("kind"),
                "symbol": r.get("symbol"),
                "recurrence": r.get("recurrence") or "none",
            }
        )
    return json.dumps(
        {"days": span, "count": len(items), "events": items}, ensure_ascii=False
    )
