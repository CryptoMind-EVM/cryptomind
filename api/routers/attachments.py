"""對話附圖讀取 API（vision Phase 2，docs/plans/2026-08-30-vision-image-storage-design.md）.

GET /api/attachments/{id}——owner-only（非本人/不存在一律 404，不洩漏存在性）。
歷史重播 <img> 與 lightbox 使用；Cache-Control: private（內容含使用者資料，
僅允許瀏覽器私有快取）。寫入不在此路由：上傳走 analyze / telegram_chat 的
既有附圖流程（validate → describe → store_image_attachment）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from core.orm.chat_attachments_repo import chat_attachments_repo
from core.orm.session import get_async_session

router = APIRouter()


@router.get("/api/attachments/{attachment_id}")
@limiter.limit("60/minute")
async def get_attachment(
    request: Request,
    attachment_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """owner-only 讀取壓縮圖（歷史重播縮圖/lightbox）。"""
    attachment = await chat_attachments_repo.get_owned(
        db, attachment_id, current_user.get("user_id", "")
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="ATTACHMENT_NOT_FOUND")
    return Response(
        content=attachment.data,
        media_type=f"image/{attachment.mime}",
        headers={"Cache-Control": "private, max-age=86400"},
    )
