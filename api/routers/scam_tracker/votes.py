"""
可疑錢包追蹤系統 - 投票 API
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core.database.scam_tracker import vote_scam_report

from .models import VoteRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/votes", tags=["Scam Tracker - Votes"])


@router.post("/{report_id}", response_model=dict)
@limiter.limit("20/minute")
async def vote_on_report(
    report_id: int,
    request: Request,
    req: VoteRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    對舉報投票

    支持 Toggle 切換：
    - 點擊相同類型 = 取消投票
    - 點擊不同類型 = 切換投票

    需要登入，舉報者本人不能對自己的舉報投票。
    """
    try:
        user_id = current_user.get("user_id")

        result = await run_sync(
            lambda: vote_scam_report(
                report_id=report_id, user_id=user_id, vote_type=req.vote_type
            )
        )

        if result.get("success"):
            action = result.get("action")
            action_messages = {
                "voted": f"{'Approved' if req.vote_type == 'approve' else 'Opposed'} successfully",
                "cancelled": f"{'Approval' if req.vote_type == 'approve' else 'Opposition'} cancelled",
                "switched": f"Switched to {'approval' if req.vote_type == 'approve' else 'opposition'}",
            }

            return {
                "success": True,
                "action": action,
                "message": action_messages.get(action, "Vote successful"),
            }
        else:
            error = result.get("error")

            if error == "report_not_found":
                raise HTTPException(status_code=404, detail="Report not found")
            elif error == "cannot_vote_own_report":
                raise HTTPException(status_code=403, detail="You cannot vote on your own report")
            elif error == "vote_too_fast":
                raise HTTPException(status_code=429, detail="Voting too frequently, please try again later")
            else:
                raise HTTPException(status_code=500, detail=f"Vote failed: {error}")

    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Vote on scam report failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Vote failed, please try again later")
