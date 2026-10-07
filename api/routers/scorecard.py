"""判斷評分 API（本人）。

- ``GET /api/journal/scorecard``：主數字、分組彙總（不滿 10 筆遮罩）、筆數、最近 20 筆
- ``GET /api/journal/scorecard/entries?status=&limit=``：明細
- ``POST /api/journal/scorecard/refresh``：立刻建檔＋評分自己的（1 次／分；平常靠每日 cron）

設計：docs/plans/2026-09-13-judgment-scoring-design.md
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core.scorecard import calls, service, store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/journal/scorecard", tags=["scorecard"])


@router.get("")
@limiter.limit("30/minute")
async def get_scorecard(
    request: Request, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    try:
        data = await run_sync(lambda: service.scorecard_for_user(user_id))
        return {"success": True, **data}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scorecard] load failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to load scorecard")


@router.get("/entries")
@limiter.limit("30/minute")
async def get_entries(
    request: Request,
    status: Optional[str] = Query(None, pattern="^(pending|scored|unscorable)$"),
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    try:
        rows = await run_sync(lambda: store.rows_for_user(user_id, limit=2000))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scorecard] entries failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to load scorecard entries")
    if status:
        rows = [r for r in rows if r["status"] == status]
    return {"success": True, "entries": rows[:limit], "count": len(rows)}


@router.post("/refresh")
@limiter.limit("1/minute")
async def refresh_scorecard(
    request: Request, current_user: dict = Depends(get_current_user)
):
    """手動：建檔＋對自己到期的列評分（抓價要幾秒，所以限一分鐘一次）。"""
    user_id = current_user["user_id"]
    try:
        created = await run_sync(lambda: service.build_pending_for_user(user_id))
        summary = await run_sync(
            lambda: service.score_due_for_user(
                user_id, datetime.now(timezone.utc).date()
            )
        )
        data = await run_sync(lambda: service.scorecard_for_user(user_id))
        return {"success": True, "created": created, "scoring": summary, **data}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scorecard] refresh failed user=%s: %s", user_id, exc)
        raise HTTPException(
            status_code=500, detail="Scorecard refresh failed, please try again later"
        )


# ── v2 明確喊單（設計 §7）────────────────────────────────────────────


class CallInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    side: str = Field(pattern="^(bullish|bearish|buy|sell|long|short)$")
    horizon_days: int = Field(default=30, ge=7, le=365)
    market: str = Field(default="", max_length=16)
    target_price: Optional[float] = Field(default=None, gt=0)
    note: str = Field(default="", max_length=500)


@router.get("/calls")
@limiter.limit("30/minute")
async def list_calls(request: Request, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    try:
        rows = await run_sync(lambda: calls.list_calls(user_id))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scorecard] list calls failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to load calls")
    return {
        "success": True,
        "calls": rows,
        "cancel_window_hours": calls.CANCEL_WINDOW_HOURS,
    }


@router.post("/calls", status_code=201)
@limiter.limit("10/minute")
async def create_call(
    request: Request, body: CallInput, current_user: dict = Depends(get_current_user)
):
    """帳本頁直接記一筆判斷（聊天那條走 record_call 工具＋確認卡）。"""
    user_id = current_user["user_id"]
    try:
        row = await run_sync(
            lambda: calls.create_call(
                user_id,
                symbol=body.symbol,
                side=body.side,
                market=body.market,
                horizon_days=body.horizon_days,
                target_price=body.target_price,
                note=body.note,
                source="web",
            )
        )
    except calls.CallError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scorecard] create call failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to record the call")
    return {"success": True, "call": row}


@router.delete("/calls/{call_id}")
@limiter.limit("10/minute")
async def cancel_call(
    request: Request, call_id: int, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    try:
        result = await run_sync(lambda: calls.cancel_call(user_id, call_id))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[scorecard] cancel call failed user=%s id=%s: %s", user_id, call_id, exc
        )
        raise HTTPException(status_code=500, detail="Failed to cancel the call")
    if not result.get("ok"):
        code = 404 if result.get("error") == "not found" else 409
        raise HTTPException(
            status_code=code, detail=result.get("error", "cannot cancel")
        )
    return {"success": True}
