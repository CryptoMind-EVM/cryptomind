"""
Forum post API endpoints.
"""

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.exc import IntegrityError

from api import payment_rails
from api.deps import get_current_user, resolve_request_token, verify_token
from api.middleware.rate_limit import limiter
from api.routers.notifications import push_notifications_read
from api.utils import run_sync
from core.config import FORUM_POST_FEE_USD, TEST_MODE, TEST_USER
from core.database import check_daily_post_limit, get_user_membership
from core.moderation.service import check_post
from core.moderation.service import record as record_moderation
from core.orm.forum_repo import forum_repo
from core.orm.notifications_repo import notifications_repo

from .models import CheckPostRequest, CreatePostRequest, UpdatePostRequest
from .payments import (
    PLAN_POST,
    author_evm_addresses,
    bound_payers,
    ensure_usdc_payments_open,
    issue_order,
    load_order,
    verify_order_payment,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forum/posts", tags=["Forum - Posts"])
oauth2_scheme_optional = OAuth2PasswordBearer(
    tokenUrl="/api/user/login", auto_error=False
)

VALID_CATEGORIES = ["analysis", "question", "tutorial", "news", "chat", "insight"]


@router.get("")
async def list_posts(
    board: Optional[str] = Query(None, description="Board slug"),
    category: Optional[str] = Query(None, description="Post category"),
    tag: Optional[str] = Query(None, description="Post tag"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    try:
        board_id = None
        if board:
            board_info = await forum_repo.get_board_by_slug(board)
            if not board_info:
                raise HTTPException(status_code=404, detail="Board not found")
            board_id = board_info["id"]

        posts = await forum_repo.get_posts(
            board_id=board_id,
            category=category,
            tag=tag,
            limit=limit,
            offset=offset,
        )
        return {"success": True, "posts": posts, "count": len(posts)}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load posts")


@router.post("/payment-order")
@limiter.limit("20/minute")
async def create_post_payment_order(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    發文費訂單（免費會員）：USDC on Base 付給平台收款地址。金額＝FORUM_POST_FEE_USD
    ＋唯一尾數，簽進訂單 token。2026-09-25 取代 TON 的 /ton-order。
    """
    user_id = current_user["user_id"]
    if FORUM_POST_FEE_USD <= 0:
        raise HTTPException(status_code=400, detail="Posting is free right now")
    ensure_usdc_payments_open(request)

    membership = await run_sync(get_user_membership, user_id)
    if membership.get("is_premium"):
        raise HTTPException(status_code=400, detail="Premium members post for free")
    # 錢轉出去之前就擋：額度用完或沒綁付款錢包，付了也發不了文
    limit_check = await run_sync(check_daily_post_limit, user_id)
    if not limit_check["allowed"]:
        raise HTTPException(
            status_code=429,
            detail=f"Daily post limit reached ({limit_check['limit']})",
        )
    await bound_payers(user_id)

    return issue_order(
        user_id,
        PLAN_POST,
        float(FORUM_POST_FEE_USD),
        payment_rails.EVM_USDC_RECEIVING_ADDRESS,
    )


@router.post("/check")
@limiter.limit("30/minute")
async def check_post_content(
    request: Request,
    body: CheckPostRequest,
    current_user: dict = Depends(get_current_user),
):
    """發文頁即時檢查（打字停下來才呼叫）：通過才亮綠燈、才能發文。"""
    verdict = await check_post(body.title, body.content)
    return {
        "success": True,
        "status": verdict["status"],
        "reasons": verdict["reasons"],
        "signals": verdict["signals"] if verdict["status"] == "block" else [],
        "category": verdict["category"] if verdict["status"] == "block" else None,
    }


@router.post("")
@limiter.limit("20/minute")
async def create_new_post(
    request: Request,
    body: CreatePostRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["user_id"]

        if body.category not in VALID_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category. Allowed: {', '.join(VALID_CATEGORIES)}",
            )

        board = await forum_repo.get_board_by_slug(body.board_slug)
        if not board:
            raise HTTPException(status_code=404, detail="Board not found")
        if not board["is_active"]:
            raise HTTPException(status_code=400, detail="Board is not active")

        membership = await run_sync(get_user_membership, user_id)

        limit_check = await run_sync(check_daily_post_limit, user_id)
        if not limit_check["allowed"]:
            raise HTTPException(
                status_code=429,
                detail=f"Daily post limit reached ({limit_check['limit']})",
            )

        # 付款驗證之前擋：先付了錢才被擋，錢就卡住了（發文頁也是先檢查通過才進付款）
        verdict = await check_post(body.title, body.content)
        if verdict["status"] == "block":
            record_moderation(user_id, verdict, "create", "post")
            raise HTTPException(status_code=422, detail="content_blocked")

        is_test_user = TEST_MODE and (
            user_id.startswith("test-user-") or user_id == TEST_USER.get("uid")
        )

        if not membership["is_premium"] and FORUM_POST_FEE_USD > 0:
            if is_test_user and not body.payment_tx_hash and not body.order_token:
                body.payment_tx_hash = f"test_post_{int(time.time() * 1000)}"
                logger.info(
                    "TEST_MODE: Bypassing payment requirement for user %s", user_id
                )
            elif body.order_token:
                # USDC on Base 發文費：伺服器簽的訂單＋鏈上驗證（精確金額、付給平台、
                # 付款人是自己綁定的錢包、確認數、訂單開立後才付）。tx hash 由驗證
                # 結果決定（小寫），寫進 UNIQUE 的 payment_tx_hash 擋重放。
                order = load_order(body.order_token, user_id, PLAN_POST)
                payers = await bound_payers(user_id)
                body.payment_tx_hash = await verify_order_payment(
                    order, payers, body.payment_tx_hash
                )
            else:
                # 自報的 payment_tx_hash 從沒上鏈驗證過（只靠 UNIQUE index 擋重用）——
                # 免費會員只能走上面的簽章訂單＋鏈上驗證
                raise HTTPException(
                    status_code=402,
                    detail="Free members must complete payment before posting",
                )
        else:
            # Premium 不用付費，發文費設 0（冷啟動免費）時人人免付；客戶端塞的 hash
            # 不寫進 UNIQUE 欄位（否則能佔用別人的 tx）
            body.payment_tx_hash = None

        try:
            result = await forum_repo.create_post(
                board_id=board["id"],
                user_id=user_id,
                category=body.category,
                title=body.title,
                content=body.content,
                tags=body.tags,
                payment_tx_hash=body.payment_tx_hash,
            )
        except IntegrityError:
            if body.payment_tx_hash:
                # posts.payment_tx_hash UNIQUE：同一筆付款第二次發文
                raise HTTPException(
                    status_code=409, detail="Payment has already been used"
                )
            raise

        if not result["success"]:
            if result.get("error") == "daily_post_limit_reached":
                raise HTTPException(
                    status_code=429,
                    detail=f"Daily post limit reached ({result['limit']})",
                )
            raise HTTPException(
                status_code=500, detail=result.get("error", "Failed to create post")
            )

        record_moderation(user_id, verdict, "create", "post", result["post_id"])
        return {
            "success": True,
            "message": "Post created successfully",
            "post_id": result["post_id"],
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create post")


async def _clear_post_notifications(user_id: str, post_id: int) -> None:
    """打開文章＝看過這篇的推／留言：鈴鐺與論壇紅點跟著清（同讀了群組清群組通知）。失敗不影響看文章"""
    try:
        ids = await notifications_repo.mark_post_notifications_read(user_id, post_id)
        if ids:
            await push_notifications_read(user_id, ids)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"Failed to clear post notifications: {e}")


async def _clear_deleted_post_notifications(post_id: int) -> None:
    """文章刪除了：這篇的論壇通知對所有人變已讀並推給各人的鈴鐺（按進去只會 404、紅點也不該被釘住）。
    失敗不影響刪文"""
    try:
        cleared = await notifications_repo.resolve_post_notifications(post_id)
        for uid, ids in cleared.items():
            await push_notifications_read(uid, ids)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"Failed to clear deleted post notifications: {e}")


@router.get("/{post_id}")
async def get_post_detail(
    post_id: int,
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme_optional),
):
    try:
        viewer_user_id = None
        resolved_token = resolve_request_token(request, token)
        if resolved_token:
            try:
                viewer_user_id = verify_token(resolved_token).get("sub")
            except HTTPException:
                viewer_user_id = None

        post = await forum_repo.get_post_by_id(
            post_id, increment_view=True, viewer_user_id=viewer_user_id
        )
        if not post or post["is_hidden"]:
            # 通知點進來才發現文章已刪除／被隱藏：留在鈴鐺的通知順手清掉，不然論壇紅點永遠在
            if viewer_user_id:
                await _clear_post_notifications(viewer_user_id, post_id)
            raise HTTPException(
                status_code=404,
                detail="Post not found" if not post else "Post has been hidden",
            )

        if viewer_user_id:
            await _clear_post_notifications(viewer_user_id, post_id)

        # 前端據此決定要不要開放打賞（USDC on Base 付給作者；只給布林，不外露地址）
        tippable = bool(await author_evm_addresses(post["user_id"]))
        return {"success": True, "post": {**post, "author_tippable": tippable}}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load post")


@router.put("/{post_id}")
@limiter.limit("10/minute")
async def update_post_content(
    post_id: int,
    request: Request,
    req: UpdatePostRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["user_id"]

        if req.category and req.category not in VALID_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category. Allowed: {', '.join(VALID_CATEGORIES)}",
            )

        # 先發乾淨的再改成詐騙文：編輯也要過同一道檢查。只改標題或只改內文時，跟原本的另一半
        # 合起來檢查——不然詐騙內容拆在標題、內文兩次編輯，模型永遠只看到半篇（review 2026-10-01）
        verdict = None
        if req.title or req.content:
            title, content = req.title, req.content
            if title is None or content is None:
                existing = await forum_repo.get_post_by_id(
                    post_id, increment_view=False
                )
                if existing:
                    title = existing.get("title") if title is None else title
                    content = existing.get("content") if content is None else content
            verdict = await check_post(title or "", content or "")
            if verdict["status"] == "block":
                record_moderation(user_id, verdict, "update", "post", post_id)
                raise HTTPException(status_code=422, detail="content_blocked")

        success = await forum_repo.update_post(
            post_id=post_id,
            user_id=user_id,
            title=req.title,
            content=req.content,
            category=req.category,
        )

        if not success:
            raise HTTPException(status_code=403, detail="Cannot edit this post")

        if verdict:
            record_moderation(user_id, verdict, "update", "post", post_id)
        return {"success": True, "message": "Post updated"}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update post")


@router.delete("/{post_id}")
@limiter.limit("10/minute")
async def delete_post_by_id(
    request: Request, post_id: int, current_user: dict = Depends(get_current_user)
):
    try:
        user_id = current_user["user_id"]

        success = await forum_repo.delete_post(post_id=post_id, user_id=user_id)

        if not success:
            raise HTTPException(status_code=403, detail="Cannot delete this post")

        await _clear_deleted_post_notifications(post_id)
        return {"success": True, "message": "Post deleted"}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete post")
