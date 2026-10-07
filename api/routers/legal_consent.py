"""條款改版後的同意紀錄（2026-09-27；版本號與流程見 core/legal.py）。

POST /api/user/legal/accept {version} —— 只收目前的 LEGAL_VERSION：頁面開著時條款
剛好又改版，送上來的是舊版號 → 409，前端請使用者重新整理後再看一次新版。
狀態由 /api/user/me 的 ``legal`` 帶給前端（不另開 GET，登入後不多一個請求）。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core import legal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/legal", tags=["Legal"])


class LegalAcceptInput(BaseModel):
    version: str = Field(..., min_length=1, max_length=32)


@router.post("/accept")
@limiter.limit("10/minute")
async def accept_legal(
    request: Request,
    body: LegalAcceptInput,
    current_user: dict = Depends(get_current_user),
):
    if body.version != legal.LEGAL_VERSION:
        raise HTTPException(
            status_code=409,
            detail="The terms have been updated. Please reload the page and review them again.",
        )
    user_id = current_user["user_id"]
    try:
        await run_sync(legal.record_acceptance, user_id, body.version)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[legal] record acceptance failed user=%s: %s", user_id, type(exc).__name__
        )
        raise HTTPException(status_code=500, detail="Failed to record your consent")

    # /api/user/me 有 30 秒快取，帶著 legal.accepted=false——同意後立刻失效
    from api.routers.user import _ME_CACHE

    _ME_CACHE.pop(user_id, None)
    logger.info("[legal] accepted user=%s version=%s", user_id, body.version)
    return {"success": True, "version": body.version}
