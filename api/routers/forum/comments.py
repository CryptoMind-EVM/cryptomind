"""
回覆相關 API（推/噓/一般回覆）
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.routers.notifications import push_notification_to_user
from api.utils import run_sync
from core.database import get_daily_comment_count
from core.moderation.service import check_post
from core.moderation.service import record as record_moderation
from core.orm.forum_repo import forum_repo
from core.orm.notifications_repo import notifications_repo

from .models import AddCommentRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forum/posts", tags=["Forum - Comments"])


# 有效的回覆類型
VALID_COMMENT_TYPES = ["push", "boo", "comment"]


async def _notify(
    to_user_id: str, kind: str, post: dict, post_id: int, actor: dict
) -> None:
    """論壇通知（notify_post_activity 會合併同一篇的）；失敗不影響留言本身"""
    try:
        notification = await notifications_repo.notify_post_activity(
            to_user_id=to_user_id,
            kind=kind,
            post_id=post_id,
            post_title=post["title"],
            from_user_id=actor["user_id"],
            from_name=actor.get("username") or actor["user_id"],
        )
        if notification:
            await push_notification_to_user(to_user_id, notification)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"Failed to send forum {kind} notification: {e}")


async def _notify_vote(
    post: dict, post_id: int, reaction: str, result: dict, actor: dict
) -> None:
    """推下去才通知作者：取消推不發（以前會再發一次）、噓不發（負評通知只會引戰）、推自己的不發"""
    if (
        reaction == "push"
        and result.get("action") == "voted"
        and post["user_id"] != actor["user_id"]
    ):
        await _notify(post["user_id"], "push", post, post_id, actor)


async def _notify_comment(post: dict, post_id: int, actor: dict) -> None:
    """留言：作者收「留言了你的文章」；在這篇留過言的其他人收「也在這篇留言」（自己、作者不重複）"""
    author, user_id = post["user_id"], actor["user_id"]
    if author != user_id:
        await _notify(author, "comment", post, post_id, actor)
    try:
        others = await forum_repo.recent_commenter_ids(
            post_id, exclude={author, user_id}
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"Failed to load commenters for post {post_id}: {e}")
        return
    for uid in others:
        await _notify(uid, "thread_reply", post, post_id, actor)


@router.get("/{post_id}/comments")
async def list_comments(
    post_id: int,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
):
    """
    獲取文章的回覆列表
    """
    try:
        post = await forum_repo.get_post_by_id(post_id, increment_view=False)
        if not post or post["is_hidden"]:
            raise HTTPException(status_code=404, detail="Post not found")

        comments = await forum_repo.get_comments(post_id, limit=limit, offset=offset)
        return {"success": True, "comments": comments, "count": len(comments)}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Failed to fetch comments, please try again later"
        )


@router.post("/{post_id}/comments")
@limiter.limit("30/minute")
async def add_new_comment(
    request: Request,
    post_id: int,
    body: AddCommentRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    新增回覆

    類型：
    - push: 推（支持）
    - boo: 噓（反對）
    - comment: 一般回覆

    限制：
    - 免費會員每日回覆上限從 /api/config/limits 獲取
    - Premium 會員無限制
    """
    try:
        user_id = current_user["user_id"]

        if body.type not in VALID_COMMENT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid comment type, valid options: {', '.join(VALID_COMMENT_TYPES)}",
            )

        post = await forum_repo.get_post_by_id(post_id, increment_view=False)
        if not post or post["is_hidden"]:
            raise HTTPException(status_code=404, detail="Post not found")

        # 留言也過發文那道內容檢查（2026-10-01）：詐騙最常出現在留言區
        verdict = await check_post("", body.content) if body.content else None
        if verdict and verdict["status"] == "block":
            record_moderation(user_id, verdict, "comment", "post", post_id)
            raise HTTPException(status_code=422, detail="content_blocked")

        # 每日回覆上限只算一般留言（推噓不算）；計數在 forum_repo.add_comment 裡 +1
        if body.type == "comment":
            daily = await run_sync(get_daily_comment_count, user_id)
            if daily["remaining"] == 0:
                raise HTTPException(
                    status_code=429,
                    detail=f"Daily comment limit reached ({daily['limit']})",
                )

        result = await forum_repo.add_comment(
            post_id=post_id,
            user_id=user_id,
            comment_type=body.type,
            content=body.content,
            parent_id=body.parent_id,
        )

        if not result["success"]:
            raise HTTPException(
                status_code=500, detail=result.get("error", "Failed to add comment")
            )
        if verdict:
            record_moderation(
                user_id, verdict, "comment", "comment", result["comment_id"]
            )

        if body.type == "comment":
            await _notify_comment(post, post_id, current_user)
        else:
            await _notify_vote(post, post_id, body.type, result, current_user)

        return {
            "success": True,
            "message": "Comment added successfully",
            "comment_id": result["comment_id"],
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Failed to add comment, please try again later"
        )


async def _react_post(
    post_id: int,
    reaction: str,
    user_id: str,
    content: Optional[str],
    current_user: dict,
):
    """推/噓文共用邏輯"""
    post = await forum_repo.get_post_by_id(post_id, increment_view=False)
    if not post or post["is_hidden"]:
        raise HTTPException(status_code=404, detail="Post not found")

    # 推噓帶的那句話也過內容檢查
    if content:
        verdict = await check_post("", content)
        if verdict["status"] == "block":
            record_moderation(user_id, verdict, reaction, "post", post_id)
            raise HTTPException(status_code=422, detail="content_blocked")

    result = await forum_repo.add_comment(
        post_id=post_id, user_id=user_id, comment_type=reaction, content=content
    )

    if not result["success"]:
        raise HTTPException(
            status_code=500, detail=result.get("error", f"{reaction} failed")
        )

    await _notify_vote(
        post, post_id, reaction, result, {**current_user, "user_id": user_id}
    )

    labels = {"push": "Push", "boo": "Boo"}
    return {"success": True, "message": f"{labels.get(reaction, reaction)} successful"}


@router.post("/{post_id}/push")
@limiter.limit("30/minute")
async def push_post(
    request: Request,
    post_id: int,
    content: str = Query(None, max_length=100, description="推文內容（選填）"),
    current_user: dict = Depends(get_current_user),
):
    """推文（快捷方式）"""
    try:
        user_id = current_user["user_id"]
        return await _react_post(post_id, "push", user_id, content, current_user)
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Failed to push, please try again later"
        )


@router.post("/{post_id}/boo")
@limiter.limit("30/minute")
async def boo_post(
    request: Request,
    post_id: int,
    content: str = Query(None, max_length=100, description="噓文內容（選填）"),
    current_user: dict = Depends(get_current_user),
):
    """噓文（快捷方式）"""
    try:
        user_id = current_user["user_id"]
        return await _react_post(post_id, "boo", user_id, content, current_user)
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(
            status_code=500, detail="Failed to boo, please try again later"
        )


@router.get("/{post_id}/comment-status")
async def get_comment_status(
    post_id: int, current_user: dict = Depends(get_current_user)
):
    """
    獲取用戶在該文章的回覆狀態（今日剩餘回覆數等）
    """
    try:
        user_id = current_user["user_id"]

        daily_count = await run_sync(get_daily_comment_count, user_id)
        return {
            "success": True,
            "today_count": daily_count["count"],
            "daily_limit": daily_count["limit"],
            "remaining": daily_count["remaining"],
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch comment status, please try again later",
        )
