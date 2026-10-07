"""新手三步清單的完成狀態（聊天首頁用，PR-7）。

- ``GET /api/user/onboarding-status``：``{holding, brief, call, telegram_bound}``，
  全部從既有資料推（core/onboarding.py），不寫任何東西。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core import onboarding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user", tags=["onboarding"])


@router.get("/onboarding-status")
@limiter.limit("30/minute")
async def get_onboarding_status(
    request: Request, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    try:
        status = await run_sync(onboarding.onboarding_status, user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[onboarding] status failed user=%s: %s", user_id, type(exc).__name__
        )
        raise HTTPException(status_code=500, detail="Failed to load onboarding status")
    return {"success": True, "status": status}
