"""後台設定中心：所有旗標與數值參數的實際值（core/admin_settings.py），
以及白名單內開關／額度的後台覆寫（core/setting_overrides.py；約 15 秒內各服務生效）。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import require_admin
from api.middleware.rate_limit import limiter
from api.utils import logger, run_sync
from core import setting_overrides
from core.admin_settings import build_settings_center

router = APIRouter(tags=["Admin - Settings Center"])


class OverrideInput(BaseModel):
    value: str = Field(..., min_length=1, max_length=32)


@router.get("/settings-center")
async def admin_settings_center(admin_user: dict = Depends(require_admin)):
    # 讀 DB 覆寫表與共用快取（Redis）都是同步呼叫，放到 worker thread
    return {"success": True, **(await run_sync(build_settings_center))}


@router.put("/settings-center/overrides/{key}")
@limiter.limit("30/minute")
async def admin_set_override(
    key: str,
    request: Request,
    body: OverrideInput,
    admin_user: dict = Depends(require_admin),
):
    if not setting_overrides.is_overridable(key):
        raise HTTPException(status_code=404, detail="This setting cannot be changed here")
    try:
        value = await run_sync(
            setting_overrides.set_override, key, body.value, admin_user["user_id"]
        )
    except setting_overrides.InvalidOverride as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[overrides] set %s failed: %s", key, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to save the override")
    return {"success": True, "key": key, "value": value}


@router.delete("/settings-center/overrides/{key}")
@limiter.limit("30/minute")
async def admin_clear_override(
    key: str, request: Request, admin_user: dict = Depends(require_admin)
):
    if not setting_overrides.is_overridable(key):
        raise HTTPException(status_code=404, detail="This setting cannot be changed here")
    try:
        removed = await run_sync(
            setting_overrides.clear_override, key, admin_user["user_id"]
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[overrides] clear %s failed: %s", key, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to clear the override")
    return {"success": True, "key": key, "removed": removed}
