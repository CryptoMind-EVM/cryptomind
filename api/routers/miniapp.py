"""Base App／Farcaster mini app：manifest、通知 webhook、token 綁定。

- ``GET  /.well-known/farcaster.json``：manifest（``MINIAPP_ENABLED=false`` 時 404）。
- ``POST /api/miniapp/webhook``：宿主送來的事件（JFS 簽名；驗簽＋Key Registry 才收）。
- ``POST /api/miniapp/notifications/register``：前端登入後回報 ``context.client.notificationDetails``，
  把 fid 綁到 user_id（webhook 事件只有 fid）。
- ``GET  /api/miniapp/notifications/status``：這個使用者有沒有可用的通知 token。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.public_base import resolve_public_base
from api.utils import run_sync
from core import miniapp
from core.miniapp_notifications import service, store
from core.miniapp_notifications.jfs import JfsError
from core.tools.url_fetch import is_safe_url

logger = logging.getLogger(__name__)
router = APIRouter(tags=["miniapp"])


@router.get("/.well-known/farcaster.json")
async def farcaster_manifest(request: Request):
    if not miniapp.enabled():
        raise HTTPException(status_code=404, detail="mini app disabled")
    base = resolve_public_base(request)
    return JSONResponse(
        miniapp.build_manifest(base),
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.post("/api/miniapp/webhook")
@limiter.limit("120/minute")
async def miniapp_webhook(request: Request):
    """宿主 POST 的事件。回 4xx 宿主不會重送、5xx 會；驗不了（RPC 壞）回 503 讓它重送。"""
    if not miniapp.enabled() or not miniapp.notifications_enabled():
        raise HTTPException(status_code=404, detail="notifications disabled")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid json")
    try:
        event = await run_sync(service.parse_event, body)
    except JfsError as exc:
        if "unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="verification unavailable")
        logger.info("[miniapp] webhook rejected: %s", exc)
        raise HTTPException(status_code=401, detail="invalid signature")
    result = await run_sync(service.handle_event, event)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "bad event"))
    return {"success": True, "event": result.get("event")}


class RegisterInput(BaseModel):
    fid: int = Field(gt=0)
    url: Optional[str] = Field(default=None, max_length=1024)
    token: Optional[str] = Field(default=None, max_length=256)


@router.post("/api/miniapp/notifications/register")
@limiter.limit("30/minute")
async def register_notification_token(
    request: Request,
    body: RegisterInput,
    current_user: dict = Depends(get_current_user),
):
    """前端在宿主裡登入後回報：這個 fid 是我；有帶 notificationDetails 就順便存。"""
    user_id = current_user["user_id"]
    if body.url and body.token:
        if not body.url.startswith("https://"):
            raise HTTPException(status_code=400, detail="url must be https")
        # 這個 url 之後由 cron 直接 POST：不可指向內網／本機／雲端 metadata（SSRF）
        safe, _reason = await run_sync(is_safe_url, body.url)
        if not safe:
            raise HTTPException(status_code=400, detail="url not allowed")
        await run_sync(
            lambda: store.upsert_token(body.fid, body.url, body.token, user_id=user_id)
        )
    else:
        await run_sync(store.link_user, body.fid, user_id)
    return {"success": True, "status": await run_sync(store.status_for_user, user_id)}


@router.get("/api/miniapp/notifications/status")
async def notification_status(current_user: dict = Depends(get_current_user)):
    return {
        "success": True,
        "status": await run_sync(store.status_for_user, current_user["user_id"]),
    }
