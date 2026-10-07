"""AI 回答快照分享 API（c072；旗標 CONVERSATION_SHARE_ENABLED，**預設關**）。

流程：使用者在對話裡選一輪問答 →（預覽：看遮蔽後的內容）→ 建立 → 拿到 /s/<token> 連結 → 別人免登入唯讀看。
安全邊界：
- 內容由伺服器從資料庫取（不接受前端傳來的答案），而且 session 必須是自己的；
- 存進去前遮蔽錢包地址／交易哈希／Email（core/answer_share.redact），預覽與建立走同一個函式；
- token 只在建立當下回傳一次、資料庫只存 SHA-256；格式不對的 token 不進資料庫；
- 30 天到期、可撤銷、每人每日上限；公開頁與 JSON 一律 noindex、no-store（撤銷要立刻生效）；
- 旗標關閉時所有端點（含公開頁）一律 404。
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.public_base import resolve_public_base
from api.utils import logger, run_sync
from core import answer_share as core
from core.database.chat import check_session_ownership, find_turn
from core.feature_flags import answer_share_enabled
from core.orm.answer_share_repo import ShareLimitError, answer_share_repo

_PAGE_PATH = "web/answer-share.html"
_PUBLIC_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found")


def _require_enabled() -> None:
    """旗標關閉時一律 404。掛在 router 層：比登入驗證、body 驗證都早跑，
    關閉時未登入不會回 401、body 錯誤不會回 422（那會暴露這些路由存在）。"""
    if not answer_share_enabled():
        raise _not_found()


router = APIRouter(dependencies=[Depends(_require_enabled)])


class ShareRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    # 前端只傳「問題」來指認哪一輪；答案一律由伺服器取
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("session_id", "question")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        # NUL 會讓 psycopg2 丟 ValueError（變 500）；正常使用者不會有
        if "\x00" in value:
            raise ValueError("invalid character")
        return value


def _daily_limit() -> int:
    try:
        return max(
            1, int(os.getenv("ANSWER_SHARE_DAILY_LIMIT", core.DEFAULT_DAILY_LIMIT))
        )
    except ValueError:
        return core.DEFAULT_DAILY_LIMIT


async def _snapshot(user_id: str, body: ShareRequest) -> dict:
    """自己的對話裡那一輪問答（已遮蔽）。不是自己的／找不到／沒有答案一律 404（不洩漏別人的 session 存不存在）。"""
    if not await run_sync(check_session_ownership, body.session_id, user_id):
        raise _not_found()
    turn = await run_sync(find_turn, body.session_id, body.question)
    # 遮蔽與 PII 掃描是 CPU 工作（答案最多數千字、輸入先截斷），丟執行緒池，不佔 event loop
    snap = (
        await run_sync(core.prepare_snapshot, turn["question"], turn["answer"])
        if turn
        else None
    )
    if snap is None:
        raise _not_found()
    return snap


@router.post("/api/share/answers/preview")
@limiter.limit("30/minute")
async def preview_answer_share(
    request: Request,
    body: ShareRequest,
    current_user: dict = Depends(get_current_user),
):
    """分享前預覽：回要公開的那份內容（遮蔽後）。不寫任何東西。"""
    snap = await _snapshot(current_user["user_id"], body)
    return {"success": True, "ttl_days": core.TTL_DAYS, **snap}


@router.post("/api/share/answers")
@limiter.limit("10/minute")
async def create_answer_share(
    request: Request,
    body: ShareRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    snap = await _snapshot(user_id, body)
    try:
        await answer_share_repo.purge_expired()  # 順手清掉早就失效的，失敗不影響建立
    except Exception as exc:  # noqa: BLE001
        logger.warning("[answer-share] purge failed: %s", exc)
    try:
        # 上限檢查與寫入在同一個交易、同一把 lock 底下（並行請求不能一起穿過上限）
        made = await answer_share_repo.create(
            user_id,
            snap["question"],
            snap["answer"],
            daily_limit=_daily_limit(),
            max_active=core.MAX_ACTIVE,
        )
    except ShareLimitError as exc:
        if exc.kind == "active":
            raise HTTPException(
                status_code=409, detail="Too many active shared links"
            ) from exc
        raise HTTPException(
            status_code=429, detail="Daily share limit reached"
        ) from exc
    logger.info("[answer-share] created id=%s user=%s", made["id"], user_id[:8])
    return {
        "success": True,
        "id": made["id"],
        "url": f"{resolve_public_base(request)}/s/{made['token']}",
        "expires_at": made["expires_at"].isoformat(),
    }


@router.get("/api/share/answers")
@limiter.limit("30/minute")
async def list_answer_shares(
    request: Request, current_user: dict = Depends(get_current_user)
):
    """自己還有效的分享（不含連結——連結只在建立當下給一次）。"""
    items = await answer_share_repo.list_active(current_user["user_id"])
    return {
        "success": True,
        "items": [
            {
                "id": i["id"],
                "question": i["question"],
                "created_at": i["created_at"].isoformat(),
                "expires_at": i["expires_at"].isoformat(),
            }
            for i in items
        ],
    }


@router.delete("/api/share/answers/{share_id}")
@limiter.limit("30/minute")
async def revoke_answer_share(
    request: Request,
    share_id: int = Path(ge=1, le=2**63 - 1),  # 超過 bigint 的數字會讓資料庫丟錯（500）
    current_user: dict = Depends(get_current_user),
):
    if not await answer_share_repo.revoke(current_user["user_id"], share_id):
        raise _not_found()  # 別人的、不存在、已撤銷：一律 404
    logger.info(
        "[answer-share] revoked id=%s user=%s", share_id, current_user["user_id"][:8]
    )
    return {"success": True}


@router.get("/api/public/share/answers/{token}")
@limiter.limit("30/minute")
async def get_public_answer_share(request: Request, token: str):
    """公開唯讀（免登入、token 即能力）。不回任何使用者資訊。"""
    data = await answer_share_repo.get_active(token)
    if data is None:
        return JSONResponse(
            {"detail": "Not found"}, status_code=404, headers=_PUBLIC_HEADERS
        )
    return JSONResponse(
        {
            "success": True,
            "question": data["question"],
            "answer": data["answer"],
            "created_at": data["created_at"].isoformat(),
        },
        headers=_PUBLIC_HEADERS,
    )


def _read_page() -> str:
    with open(_PAGE_PATH, encoding="utf-8") as fh:
        return fh.read()


@router.get("/s/{token}", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def answer_share_page(request: Request, token: str):
    """公開頁外殼：伺服器端把連結預覽 meta（標題＝問題、描述＝答案前 150 字）塞進去；
    內容本身由頁面 JS 讀公開 API 再用 textContent 顯示（不經 innerHTML）。"""
    page = await asyncio.to_thread(_read_page)
    data = await answer_share_repo.get_active(token)
    if data is None:
        return HTMLResponse(
            core.apply_share_meta(page, None, None),
            status_code=404,
            headers=_PUBLIC_HEADERS,
        )
    return HTMLResponse(
        core.apply_share_meta(page, data["question"], core.og_snippet(data["answer"])),
        headers=_PUBLIC_HEADERS,
    )
