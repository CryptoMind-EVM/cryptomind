"""
可疑錢包追蹤系統 - 評論 API
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from core.orm.config_repo import config_repo
from core.orm.repositories import user_repo
from core.orm.scam_tracker_repo import scam_tracker_repo
from core.validators import filter_sensitive_content, sanitize_description

from .models import CommentCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/comments", tags=["Scam Tracker - Comments"])


@router.get("/{report_id}", response_model=dict)
async def list_scam_comments(
    report_id: int,
    limit: int = Query(50, ge=1, le=100, description="每頁數量"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """
    獲取評論列表

    公開端點，所有用戶可查看。
    """
    try:
        comments = await scam_tracker_repo.get_comments(
            report_id=report_id, limit=limit, offset=offset
        )

        return {"success": True, "comments": comments, "count": len(comments)}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"List scam comments failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch comment list, please try again later")


@router.post("/{report_id}", response_model=dict)
@limiter.limit("10/minute")
async def add_comment_to_report(
    report_id: int,
    request: Request,
    req: CommentCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    添加評論

    僅 Premium 會員可使用，用於分享受騙經歷或補充證據。
    """
    try:
        user_id = current_user.get("user_id")

        # 驗證用戶是否存在
        user = await user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=401, detail="User not found or credentials have expired, please log in again"
            )

        # legacy add_scam_comment 的關卡，2026-03 ORM 遷移（8c11e5e）沒搬過來。
        # Premium 跟 legacy 一樣可由 system_config 關掉；is_premium 已算進到期。
        require_premium = await config_repo.get_config("scam_comment_require_pro", True)
        if require_premium and not user.get("is_premium"):
            raise HTTPException(
                status_code=403,
                detail={
                    "reason": "premium_membership_required",
                    "message": "Commenting is available only to Premium members",
                },
            )

        # 同舉報：檢查收過空白的版本，存原文（留言也把 \n 轉 <br>）。
        # 下限 10 字跟 CommentCreate／前端一致（legacy 沿用描述的 20，10–19 字會被誤擋）
        content_check = filter_sensitive_content(
            sanitize_description(req.content), min_length=10
        )
        if not content_check["valid"]:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason": "content_validation_failed",
                    "message": (
                        "Content did not pass review. Remove emails, phone "
                        "numbers, messaging handles and external links."
                    ),
                    "warnings": content_check["warnings"],
                    "codes": content_check.get("codes", []),
                },
            )

        result = await scam_tracker_repo.add_comment(
            report_id=report_id,
            user_id=user_id,
            content=req.content,
            transaction_hash=req.transaction_hash,
        )

        if result.get("success"):
            return {
                "success": True,
                "comment_id": result["comment_id"],
                "message": "Comment added successfully",
            }
        else:
            error = result.get("error")
            detail = result.get("detail", "")

            # premium／內容過濾在上面就擋了
            if error == "report_not_found":
                raise HTTPException(
                    status_code=404,
                    detail={
                        "reason": "report_not_found",
                        "message": "Report not found",
                    },
                )
            elif error == "invalid_tx_hash":
                raise HTTPException(status_code=400, detail=f"Invalid transaction hash: {detail}")
            else:
                raise HTTPException(status_code=500, detail=f"Failed to add comment: {error}")

    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Add scam comment failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add comment, please try again later")
