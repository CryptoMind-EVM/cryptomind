"""Email 早報 API（PR-8；``EMAIL_BRIEF_ENABLED`` 預設開，寄信設定不齊時與關閉相同）。

要登入（Settings 用）：
- ``GET    /api/user/email-brief``：訂閱狀態（none／pending／expired／active／unsubscribed）
- ``PUT    /api/user/email-brief``：填 email → 存成待確認＋寄確認信（3/hour/user）
- ``DELETE /api/user/email-brief``：移除（刪列）

公開、token 型（信裡的連結）。GET 只回一顆按鈕的頁面、不改任何狀態——信箱的安全掃描器
（Outlook／Proofpoint 等）會預先點開信裡每個連結，GET 若直接確認就會替別人完成訂閱、
GET 若直接退訂就會把公司信箱的使用者在第一封信就退掉。真正的動作都在 POST：
- ``GET|POST /api/email/confirm?token=``：確認（單次、48 小時）→ 小結果頁
- ``GET|POST /api/email/unsubscribe?token=``：退訂（冪等）→ 小結果頁；POST 同時是
  RFC 8058 一鍵退訂（信箱服務商照 List-Unsubscribe-Post 直接 POST 這個網址）

旗標關：除了退訂之外全部 404。退訂刻意永遠可用——旗標關掉後舊信裡的退訂連結仍要有效，
而它只會讓我們少寄、不會多寄。

設計：docs/plans/2026-09-27-pr8-email-brief-impl.md
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core.email_brief import provider, service, store
from core.email_brief.provider import mask_email
from core.email_brief.render import result_page
from core.i18n import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)


def require_email_brief_enabled() -> None:
    # 旗標開而且寄信設定齊全才開放（旗標預設開）：只開旗標、Resend 沒設好時，存了 email
    # 也寄不出確認信，使用者會永遠卡在「待確認」
    if not provider.email_brief_available():
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(
    prefix="/api/user/email-brief",
    tags=["email-brief"],
    dependencies=[Depends(require_email_brief_enabled)],
)
public_router = APIRouter(prefix="/api/email", tags=["email-brief"])

# 連結帶 token：Referer 不出站、不被快取、不被索引。不用 no-referrer：它會讓頁面上表單
# POST 的 Origin 變成 "null"，帶登入 cookie 的人會被 CSRF Origin 檢查擋掉
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "same-origin",
    "X-Robots-Tag": "noindex",
}
_PAGE_STATUS = {
    "confirm_prompt": 200,
    "unsubscribe_prompt": 200,
    "confirmed": 200,
    "unsubscribed": 200,
    "expired": 410,
    "invalid": 400,
    "error": 503,
}


class EmailBriefInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    language: Optional[str] = Field(default=None, max_length=8)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        return service.normalize_email(v)

    @field_validator("language")
    @classmethod
    def _known_language(cls, v: Optional[str]) -> Optional[str]:
        return v if v in SUPPORTED_LANGUAGES else None


def _page(
    outcome: str, language: Optional[str], *, action_url: Optional[str] = None
) -> HTMLResponse:
    return HTMLResponse(
        result_page(outcome, language, action_url=action_url),
        status_code=_PAGE_STATUS.get(outcome, 400),
        headers=_PAGE_HEADERS,
    )


@router.get("")
@limiter.limit("30/minute")
async def get_email_brief(
    request: Request, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    try:
        row = await run_sync(store.get_subscription, user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[email_brief] load failed user=%s: %s", user_id, type(exc).__name__
        )
        raise HTTPException(status_code=500, detail="Failed to load email settings")
    return {"success": True, "subscription": service.subscription_status(row)}


@router.put("")
@limiter.limit("3/hour")
async def put_email_brief(
    request: Request,
    body: EmailBriefInput,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    sender = provider.get_sender()
    if sender is None:
        raise HTTPException(
            status_code=503, detail="Email brief is not available right now"
        )
    try:
        prepared = await run_sync(service.prepare_subscription, user_id, body.email)
    except service.AddressThrottled:
        # 訊息刻意跟一般限流一樣：不透露「這個地址最近被別的帳號用過」
        raise HTTPException(
            status_code=429, detail="Too many requests. Please try again later."
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[email_brief] save failed user=%s to=%s: %s",
            user_id,
            mask_email(body.email),
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Failed to save email")

    if prepared.status == "active":
        return {
            "success": True,
            "sent": False,
            "subscription": {
                "status": "active",
                "email": prepared.email,
                "confirm_sent_at": None,
            },
        }
    if not prepared.confirm_token:
        raise HTTPException(status_code=500, detail="Failed to save email")
    language = body.language or current_user.get("language")
    sent = await service.send_confirmation(
        sender, prepared.email, prepared.confirm_token, language
    )
    if not sent:
        raise HTTPException(
            status_code=502,
            detail="Could not send the confirmation email. Please try again later.",
        )
    return {
        "success": True,
        "sent": True,
        "subscription": {
            "status": "pending",
            "email": prepared.email,
            "confirm_sent_at": None,
        },
    }


@router.delete("")
@limiter.limit("10/minute")
async def delete_email_brief(
    request: Request, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    try:
        removed = await run_sync(store.delete_for_user, user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[email_brief] remove failed user=%s: %s", user_id, type(exc).__name__
        )
        raise HTTPException(status_code=500, detail="Failed to remove email")
    logger.info("[email_brief] removed user=%s", user_id)
    return {"success": True, "removed": bool(removed)}


def _prompt(kind: str, path: str, token: str) -> HTMLResponse:
    """GET：只回一顆 POST 按鈕（不查 DB）。token 格式不對直接顯示無效。"""
    if not service.is_token_shaped(token):
        return _page("invalid", None)
    return _page(kind, None, action_url=f"{path}?{urlencode({'token': token})}")


@public_router.get("/confirm", dependencies=[Depends(require_email_brief_enabled)])
@limiter.limit("20/minute")
async def confirm_email_page(request: Request, token: str = ""):
    return _prompt("confirm_prompt", "/api/email/confirm", token)


@public_router.post("/confirm", dependencies=[Depends(require_email_brief_enabled)])
@limiter.limit("20/minute")
async def confirm_email(request: Request, token: str = ""):
    try:
        outcome, language = await run_sync(service.confirm, token)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[email_brief] confirm failed: %s", type(exc).__name__)
        outcome, language = "error", None
    return _page(outcome, language)


@public_router.get("/unsubscribe")
@limiter.limit("20/minute")
async def unsubscribe_email_page(request: Request, token: str = ""):
    return _prompt("unsubscribe_prompt", "/api/email/unsubscribe", token)


@public_router.post("/unsubscribe")
# 頁面上的按鈕＋RFC 8058 一鍵退訂都打這裡。一鍵退訂由信箱服務商（Gmail 等）的伺服器
# 送出，很多使用者共用少數 IP——上限放寬，退訂被限流擋掉是合規問題
@limiter.limit("300/minute")
async def unsubscribe_email(request: Request, token: str = ""):
    try:
        outcome, language = await run_sync(service.unsubscribe, token)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[email_brief] unsubscribe failed: %s", type(exc).__name__)
        outcome, language = "error", None
    return _page(outcome, language)
