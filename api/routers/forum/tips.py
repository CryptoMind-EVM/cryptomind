"""
Tip-related API endpoints.

2026-09-25 起打賞是 USDC on Base，直接付到作者自己的 EVM 地址（身份地址或 SIWE
綁定的錢包）——平台不經手。流程同 premium：伺服器簽的訂單（金額含唯一尾數、
收款人、文章 id）→ 錢包送出 → 帶 tx hash 領取，鏈上驗證後記帳。TON 打賞移除。
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import logger
from core.config import STABLE_DECIMALS, TEST_MODE
from core.database.forum import annotate_tip_row
from core.orm.forum_repo import forum_repo

from .models import CreateTipRequest, TipOrderRequest
from .payments import (
    PLAN_TIP,
    author_evm_addresses,
    bound_payers,
    ensure_usdc_payments_open,
    issue_order,
    load_order,
    resolve_tip_amount,
    verify_order_payment,
)

router = APIRouter(prefix="/api/forum", tags=["Forum - Tips"])


async def _tippable_post(post_id: int, user_id: str) -> dict:
    post = await forum_repo.get_post_by_id(post_id, increment_view=False)
    if not post or post["is_hidden"]:
        raise HTTPException(status_code=404, detail="Post not found")
    if post["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot tip your own post")
    return post


@router.post("/posts/{post_id}/tip/payment-order")
@limiter.limit("20/minute")
async def create_tip_payment_order(
    request: Request,
    post_id: int,
    body: TipOrderRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    打賞訂單：USDC on Base 付給作者的 EVM 地址。金額（USD，範圍由
    FORUM_TIP_MIN_USD～FORUM_TIP_MAX_USD 決定）＋唯一尾數、收款人、文章 id
    都簽進 order_token。
    """
    user_id = current_user["user_id"]
    ensure_usdc_payments_open(request)
    post = await _tippable_post(post_id, user_id)

    amount = resolve_tip_amount(body.amount)
    receivers = await author_evm_addresses(post["user_id"])
    if not receivers:
        raise HTTPException(
            status_code=400,
            detail="This author has not linked an EVM wallet, so tips are not available",
        )
    # 錢轉出去之前就擋：付款人不是綁定錢包的話，付了也領不到
    await bound_payers(user_id)

    return issue_order(
        user_id,
        PLAN_TIP,
        amount,
        receivers[0],
        extra={"post": post_id},
    )


@router.post("/posts/{post_id}/tip")
@limiter.limit("10/minute")
async def tip_post(
    request: Request,
    post_id: int,
    body: CreateTipRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["user_id"]
        post = await _tippable_post(post_id, user_id)

        if not TEST_MODE:
            if not body.order_token:
                raise HTTPException(
                    status_code=400, detail="order_token is required for tips"
                )
            order = load_order(body.order_token, user_id, PLAN_TIP)
            if order.get("post") != post_id:
                raise HTTPException(
                    status_code=400, detail="Order is not for this post"
                )
            # 領取時重查：簽進訂單的收款人還是作者「現在」的地址（下單後解綁就不認）
            receiver = str(order.get("recv") or "").lower()
            if receiver not in await author_evm_addresses(post["user_id"]):
                raise HTTPException(
                    status_code=400, detail="Tip receiver does not match post author"
                )
            payers = await bound_payers(user_id)
            tx_hash = await verify_order_payment(
                order, payers, body.tx_hash, receiving_address=receiver
            )
            # 驗證要求鏈上金額＝訂單 micro，所以實收就是它（美元，含唯一尾數）
            verified_amount = int(order["micro"]) / 10**STABLE_DECIMALS
        else:
            verified_amount = resolve_tip_amount(body.amount)
            tx_hash = body.tx_hash
            if not tx_hash:
                import uuid

                tx_hash = f"test_tip_{uuid.uuid4().hex[:16]}"

        try:
            tip_id = await forum_repo.create_tip(
                post_id=post_id,
                from_user_id=user_id,
                to_user_id=post["user_id"],
                amount=verified_amount,
                tx_hash=tx_hash,
            )
        except IntegrityError:
            # tips.tx_hash UNIQUE：同一筆轉帳第二次領取
            raise HTTPException(status_code=409, detail="Payment has already been used")

        logger.info(
            "Tip created: post=%d, from=%s, to=%s, amount=%.2f, tx=%s",
            post_id,
            user_id,
            post["user_id"],
            verified_amount,
            tx_hash[:16] if tx_hash else "none",
        )

        return {
            "success": True,
            "message": "Tip successful",
            "tip_id": tip_id,
            "amount": verified_amount,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Tip failed, please try again later"
        )


@router.get("/tips/sent")
async def get_sent_tips(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["user_id"]

        tips = [
            annotate_tip_row(t)
            for t in await forum_repo.get_tips_sent(user_id, limit=limit, offset=offset)
        ]
        return {"success": True, "tips": tips, "count": len(tips)}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get tip records")


@router.get("/tips/received")
async def get_received_tips(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["user_id"]

        tips = [
            annotate_tip_row(t)
            for t in await forum_repo.get_tips_received(
                user_id, limit=limit, offset=offset
            )
        ]
        total = await forum_repo.get_tips_total_received(user_id)
        return {
            "success": True,
            "tips": tips,
            "count": len(tips),
            "total_received": total,
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get tip records")
