"""行事曆 API（帳本分頁的「行事曆」區塊；Phase B）。

- ``GET    /api/journal/calendar?days=60``：接下來 N 天的事件（系統＋自訂）
- ``POST   /api/journal/calendar``：新增自訂事件（可每週／每月／每年重複）
- ``PUT    /api/journal/calendar/{event_id}``：編輯自訂事件（重複事件＝整個系列）
- ``DELETE /api/journal/calendar/{event_id}``：刪除（系統事件也可刪，明天同步不會再加回：dedupe_key 已存在時只更新；重複事件整個系列一起刪）

設計：docs/plans/2026-09-12-daily-brief-retention-design.md §5
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core.daily_brief import store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/journal/calendar", tags=["calendar"])

MAX_DAYS_AHEAD = 366
# 月曆一次看 6 週（42 格）；留餘裕給前後緩衝
MAX_RANGE_DAYS = 62


class CalendarEventInput(BaseModel):
    event_date: date
    title: str = Field(min_length=1, max_length=120)
    remind_days_before: int = Field(default=1, ge=0, le=30)
    note: str = Field(default="", max_length=500)
    recurrence: Literal["none", "weekly", "monthly", "yearly"] = "none"
    recurrence_until: Optional[date] = None


def _iso(value) -> Optional[str]:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value else None


def _serialize(row: dict) -> dict:
    recurrence = row.get("recurrence") or "none"
    return {
        "id": row.get("id"),
        "event_date": _iso(row.get("event_date")) or "",
        "title": row.get("title"),
        "kind": row.get("kind"),
        "symbol": row.get("symbol"),
        "market": row.get("market"),
        "source": row.get("source"),
        "remind_days_before": row.get("remind_days_before"),
        "note": row.get("note") or "",
        "recurrence": recurrence,
        "recurrence_until": _iso(row.get("recurrence_until")),
        # 重複事件的錨點日（編輯時送回這天，不是點到的那一次）
        "series_start": _iso(row.get("series_start") or row.get("event_date")),
    }


def _clean(body: CalendarEventInput) -> tuple[str, Optional[date]]:
    """標題去多餘空白；不重複就不留結束日。錯了丟 400。"""
    title = " ".join(body.title.split()).strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    until = body.recurrence_until if body.recurrence != "none" else None
    if until is not None and until < body.event_date:
        raise HTTPException(status_code=400, detail="recurrence_until before event_date")
    return title, until


def _dedupe_key(event_date: date, title: str) -> str:
    return f"user:{event_date.isoformat()}:{title.lower()[:60]}"


@router.get("")
@limiter.limit("30/minute")
async def list_calendar(
    request: Request,
    days: int = Query(60, ge=1, le=MAX_DAYS_AHEAD),
    start: Optional[date] = Query(None, description="月曆視圖：區間起日（含）"),
    end: Optional[date] = Query(None, description="月曆視圖：區間迄日（含）"),
    current_user: dict = Depends(get_current_user),
):
    """預設今天起 ``days`` 天；月曆視圖帶 ``start``／``end``（最多 ``MAX_RANGE_DAYS`` 天，可含過去）。"""
    user_id = current_user["user_id"]
    if (start is None) != (end is None):
        raise HTTPException(status_code=422, detail="start and end must be given together")
    if start is not None and end is not None:
        if end < start or (end - start).days > MAX_RANGE_DAYS:
            raise HTTPException(
                status_code=422, detail=f"range must be 0..{MAX_RANGE_DAYS} days"
            )
        range_start, range_end = start, end
    else:
        range_start = date.today()
        range_end = range_start + timedelta(days=days)
    try:
        rows = await run_sync(store.list_events_between, user_id, range_start, range_end)
        events = [_serialize(r) for r in rows]
        return {
            "success": True,
            "events": events,
            "count": len(events),
            "start": range_start.isoformat(),
            "end": range_end.isoformat(),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calendar] list failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to load calendar")


@router.post("", status_code=201)
@limiter.limit("20/minute")
async def add_calendar(
    request: Request,
    body: CalendarEventInput,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    today = date.today()
    if body.event_date < today - timedelta(
        days=1
    ) or body.event_date > today + timedelta(days=MAX_DAYS_AHEAD):
        raise HTTPException(status_code=400, detail="event_date out of range")
    title, until = _clean(body)
    try:
        row = await run_sync(
            lambda: store.add_event(
                user_id,
                event_date=body.event_date,
                title=title,
                kind="custom",
                source="user",
                remind_days_before=body.remind_days_before,
                note=body.note,
                dedupe_key=_dedupe_key(body.event_date, title),
                recurrence=body.recurrence,
                recurrence_until=until,
            )
        )
        return {"success": True, "event": _serialize(row or {})}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calendar] add failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save event")


@router.put("/{event_id}")
@limiter.limit("20/minute")
async def update_calendar(
    request: Request,
    event_id: int,
    body: CalendarEventInput,
    current_user: dict = Depends(get_current_user),
):
    """編輯自訂事件。重複事件送的是錨點日，可能早已過去，所以下限放寬到一年前。"""
    user_id = current_user["user_id"]
    today = date.today()
    if body.event_date < today - timedelta(
        days=MAX_DAYS_AHEAD
    ) or body.event_date > today + timedelta(days=MAX_DAYS_AHEAD):
        raise HTTPException(status_code=400, detail="event_date out of range")
    title, until = _clean(body)
    try:
        row = await run_sync(
            lambda: store.update_event(
                user_id,
                event_id,
                event_date=body.event_date,
                title=title,
                remind_days_before=body.remind_days_before,
                note=body.note,
                recurrence=body.recurrence,
                recurrence_until=until,
                dedupe_key=_dedupe_key(body.event_date, title),
            )
        )
    except store.DuplicateEventError:
        raise HTTPException(status_code=409, detail="same event already exists")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calendar] update failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save event")
    if not row:
        raise HTTPException(status_code=404, detail="event not found")
    return {"success": True, "event": _serialize(row)}


@router.delete("/{event_id}")
@limiter.limit("20/minute")
async def delete_calendar(
    request: Request, event_id: int, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    try:
        ok = await run_sync(store.delete_event, user_id, event_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calendar] delete failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete event")
    if not ok:
        raise HTTPException(status_code=404, detail="event not found")
    return {"success": True}
