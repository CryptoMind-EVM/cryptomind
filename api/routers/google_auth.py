"""Google 登入與綁定（docs/plans/2026-09-13-google-play-twa-design.md §4）。

- ``POST /api/user/google-login``：Google Identity Services 的 ID token → 驗簽 → 有綁定就登入那個帳號，
  沒有就建 ``g_<sub>`` 帳號並綁定。網頁版與 Play 版都走這條（Telegram 內不顯示）。
- ``POST /api/google/bind``：登入中的帳號綁 Google（Settings → 連結）。
- ``GET  /api/google/status``、``POST /api/google/unlink``：比照 Telegram。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from slowapi.util import get_remote_address

from api.deps import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    set_token_cookies,
)
from api.funnel import record_guest_converted
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core import auth_google
from core.database.google import (
    create_google_binding,
    delete_binding_by_user_id,
    get_binding_by_sub,
    get_binding_by_user_id,
    update_last_used,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["google"])


class GoogleLoginRequest(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)  # GIS 回的 ID token


def _display_name(identity: auth_google.GoogleIdentity) -> str:
    if identity.name:
        return identity.name[:64]
    if identity.email:
        return identity.email.split("@")[0][:64]
    return f"G_{identity.sub[-6:]}"


@router.post("/api/user/google-login")
@limiter.limit("20/minute")
async def google_login(request: Request, response: Response, body: GoogleLoginRequest):
    """Google 已經證明身分，不需要錢包簽章。有綁定→那個帳號；沒有→建 g_<sub>。"""
    from api.routers.user import _clear_auth_failures, _enforce_auth_lockout
    from core.auth_failure_tracker import record_auth_failure
    from core.database.user import create_or_get_user, set_user_language
    from core.i18n import language_from_client_code

    if not auth_google.enabled():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    client_ip = get_remote_address(request)
    _enforce_auth_lockout(client_ip)
    try:
        identity = await run_sync(auth_google.verify_id_token, body.credential)
    except auth_google.GoogleAuthError as exc:
        record_auth_failure(client_ip, "google id token invalid")
        raise HTTPException(status_code=401, detail=str(exc))
    if identity.email and not identity.email_verified:
        raise HTTPException(status_code=401, detail="Google email is not verified")

    try:
        binding = await run_sync(get_binding_by_sub, identity.sub)
        is_new = False
        if binding and binding.get("user_id"):
            result = await run_sync(
                lambda: create_or_get_user(identity=binding["user_id"])
            )
        else:
            user_identity = f"g_{identity.sub}"
            result = await run_sync(
                lambda: create_or_get_user(
                    identity=user_identity,
                    username=_display_name(identity),
                    auth_method="google",
                )
            )
            await run_sync(
                lambda: create_google_binding(
                    identity.sub, result["user_id"], identity.email, identity.name
                )
            )
            is_new = bool(result.get("is_new", True))
            if is_new:
                # 第一次登入就把語言偏好種下（Google 帳號語言）；早報與 Telegram 回覆讀 users.language
                await run_sync(
                    set_user_language,
                    result["user_id"],
                    language_from_client_code(identity.locale),
                )
        await run_sync(update_last_used, identity.sub)

        access_token = create_access_token(
            data={"sub": result["user_id"], "username": result["username"]}
        )
        refresh_token = create_refresh_token(
            data={"sub": result["user_id"], "username": result["username"]}
        )
        set_token_cookies(response, access_token, refresh_token)
        record_guest_converted(request, result["user_id"], "google", is_new)  # 轉換漏斗
        _clear_auth_failures(client_ip)
        return {
            "success": True,
            "user": {
                "user_id": result["user_id"],
                "username": result["username"],
                "auth_method": result.get("auth_method", "google"),
                "role": result.get("role", "user"),
                "membership_tier": result.get("membership_tier", "free"),
                "has_wallet": not str(result["user_id"]).startswith(("g_", "tg_")),
            },
            "is_new_user": is_new,
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Google 登入失敗: %s", exc)
        raise HTTPException(status_code=500, detail="Google login failed")


@router.get("/api/google/status")
async def google_status(current_user: dict = Depends(get_current_user)):
    binding = await run_sync(get_binding_by_user_id, current_user["user_id"])
    if not binding:
        return {"bound": False, "enabled": auth_google.enabled()}
    return {
        "bound": True,
        "enabled": auth_google.enabled(),
        "email": binding.get("email"),
        "display_name": binding.get("display_name"),
        "linked_at": binding.get("linked_at"),
    }


@router.post("/api/google/bind")
@limiter.limit("10/minute")
async def google_bind(
    request: Request,
    body: GoogleLoginRequest,
    current_user: dict = Depends(get_current_user),
):
    """登入中的帳號綁 Google。那個 Google 已綁在別的帳號 → 409（不做帳號合併，比照 Telegram）。"""
    if not auth_google.enabled():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    try:
        identity = await run_sync(auth_google.verify_id_token, body.credential)
    except auth_google.GoogleAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    existing = await run_sync(get_binding_by_sub, identity.sub)
    if existing and existing.get("user_id") != current_user["user_id"]:
        raise HTTPException(
            status_code=409,
            detail="This Google account is already linked to another CryptoMind account",
        )
    ok = await run_sync(
        lambda: create_google_binding(
            identity.sub, current_user["user_id"], identity.email, identity.name
        )
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to link Google account")
    return {"success": True, "bound": True, "email": identity.email}


@router.post("/api/google/unlink")
@limiter.limit("5/minute")
async def google_unlink(
    request: Request, current_user: dict = Depends(get_current_user)
):
    """解除綁定。Google 原生帳號（g_）解綁後就沒有登入方式，拒絕。"""
    if str(current_user["user_id"]).startswith("g_"):
        raise HTTPException(
            status_code=400,
            detail="This account signs in with Google; link a wallet before unlinking",
        )
    await run_sync(delete_binding_by_user_id, current_user["user_id"])
    return {"success": True, "bound": False}
