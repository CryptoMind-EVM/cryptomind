"""
好友功能 API 端點
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_optional_current_user
from api.middleware.rate_limit import limiter
from api.routers.notifications import (
    push_badges_changed,
    push_notification_to_user,
    push_notifications_read,
)
from api.utils import logger
from core.orm.friends_repo import friends_repo
from core.orm.notifications_repo import notifications_repo
from core.orm.repositories import user_repo
from core.orm.session import get_async_session

router = APIRouter()


class FriendActionRequest(BaseModel):
    target_user_id: str = Field(
        ..., min_length=1, max_length=100, description="目標用戶 ID"
    )


async def _clear_request_notifications(
    user_id: str, from_user_id: str, session: AsyncSession
) -> None:
    """from_user_id 給 user_id 的邀請處理掉了：鈴鐺那筆變已讀，推給 user_id 開著的分頁。
    通知早已讀的話沒東西可推，但待處理邀請數字變了——另外推 badges_changed 讓側欄與邀請列表重抓。
    通知失敗不影響好友動作本身。"""
    try:
        cleared = await notifications_repo.resolve_friend_request_notifications(
            user_id, from_user_id, session=session
        )
        await push_notifications_read(user_id, cleared)
        await push_badges_changed(user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("Failed to clear friend request notifications: %s", e)


# ============================================================================
# 用戶搜尋 / 發現
# ============================================================================


@router.get("/api/friends/search")
async def search_users_endpoint(
    q: str = Query(..., min_length=1, max_length=50, description="搜尋關鍵字"),
    limit: int = Query(20, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        users = await friends_repo.search_users(
            query=q,
            limit=limit,
            exclude_user_id=user_id,
            session=session,
        )

        if users:
            other_ids = [u["user_id"] for u in users]
            bulk_status = await friends_repo.get_bulk_friendship_status(
                user_id,
                other_ids,
                session=session,
            )
            for u in users:
                status = bulk_status.get(u["user_id"])
                u["friend_status"] = status.get("status") if status else None
                u["is_friend"] = status.get("status") == "accepted" if status else False
                u["is_requester"] = status.get("is_requester") if status else False
                u["blocked_by_me"] = status.get("blocked_by_me") if status else False

        return {"success": True, "users": users, "count": len(users)}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Search users failed: %s", e)
        raise HTTPException(status_code=500, detail="Search failed, please try again later")


@router.get("/api/friends/profile/{target_user_id}")
async def get_user_profile(
    target_user_id: str,
    current_user: dict | None = Depends(get_optional_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"] if current_user else None
        profile = await friends_repo.get_public_user_profile(
            target_user_id,
            viewer_user_id=user_id,
            session=session,
        )
        if not profile:
            raise HTTPException(status_code=404, detail="User not found")
        return {"success": True, "profile": profile}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Get user profile failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch profile, please try again later")


# ============================================================================
# 好友請求
# ============================================================================


@router.post("/api/friends/request")
@limiter.limit("10/minute")
async def send_request(
    request: Request,
    req: FriendActionRequest,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        target_exists = await user_repo.get_by_id(req.target_user_id, session=session)
        if not target_exists:
            raise HTTPException(status_code=404, detail="Target user not found")

        result = await friends_repo.send_friend_request(
            user_id,
            req.target_user_id,
            session=session,
        )

        if not result["success"]:
            error_messages = {
                "cannot_add_self": "You cannot add yourself as a friend",
                "already_friends": "You are already friends",
                "request_pending": "A friend request is already pending",
                "user_blocked_you": "Cannot send a request to this user",
                "you_blocked_user": "You have blocked this user. Please unblock them first",
            }
            raise HTTPException(
                status_code=400,
                detail=error_messages.get(result["error"], result["error"]),
            )

        await _clear_request_notifications(req.target_user_id, user_id, session)
        try:
            # 通知上顯示暱稱（沒設才用帳號名 EVM_xxxx）
            current_username = await user_repo.get_display_name(
                user_id, session=session
            ) or current_user.get("username", user_id)
            notification = await notifications_repo.create_notification(
                user_id=req.target_user_id,
                notification_type="friend_request",
                title="好友請求",
                body=f"{current_username} 想加你為好友",
                data={
                    "from_user_id": user_id,
                    "from_username": current_username,
                },
                session=session,
            )
            if notification:
                await push_notification_to_user(req.target_user_id, notification)
            logger.info("Friend request notification sent to %s", req.target_user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as notify_error:
            logger.warning(
                "Failed to send friend request notification: %s", notify_error
            )

        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Send friend request failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to send request, please try again later")


@router.post("/api/friends/accept")
@limiter.limit("10/minute")
async def accept_request(
    request: Request,
    req: FriendActionRequest,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        result = await friends_repo.accept_friend_request(
            user_id,
            req.target_user_id,
            session=session,
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail="Friend request not found")

        await _clear_request_notifications(user_id, req.target_user_id, session)
        try:
            # 通知上顯示暱稱（沒設才用帳號名 EVM_xxxx）
            current_username = await user_repo.get_display_name(
                user_id, session=session
            ) or current_user.get("username", user_id)
            # 自己的型別：用 friend_request 的話鈴鐺會長出接受／拒絕鈕
            notification = await notifications_repo.create_notification(
                user_id=req.target_user_id,
                notification_type="friend_accepted",
                title="好友已接受",
                body=f"{current_username} 已接受你的好友請求",
                data={
                    "from_user_id": user_id,
                    "from_username": current_username,
                },
                session=session,
            )
            if notification:
                await push_notification_to_user(req.target_user_id, notification)
            logger.info("Friend accepted notification sent to %s", req.target_user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as notify_error:
            logger.warning(
                "Failed to send friend accepted notification: %s", notify_error
            )

        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Accept friend request failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to accept request, please try again later")


@router.post("/api/friends/reject")
@limiter.limit("10/minute")
async def reject_request(
    request: Request,
    req: FriendActionRequest,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        result = await friends_repo.reject_friend_request(
            user_id,
            req.target_user_id,
            session=session,
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail="Friend request not found")

        await _clear_request_notifications(user_id, req.target_user_id, session)
        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Reject friend request failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to reject request, please try again later")


@router.post("/api/friends/cancel")
@limiter.limit("10/minute")
async def cancel_request(
    request: Request,
    req: FriendActionRequest,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        result = await friends_repo.cancel_friend_request(
            user_id,
            req.target_user_id,
            session=session,
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail="Friend request not found")

        await _clear_request_notifications(req.target_user_id, user_id, session)
        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Cancel friend request failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to cancel request, please try again later")


@router.delete("/api/friends/remove")
@limiter.limit("10/minute")
async def remove_friend_endpoint(
    request: Request,
    target_user_id: str = Query(..., description="好友的用戶 ID"),
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        result = await friends_repo.remove_friend(
            user_id,
            target_user_id,
            session=session,
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail="You are not friends")

        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Remove friend failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to remove friend, please try again later")


# ============================================================================
# 封鎖功能
# ============================================================================


@router.post("/api/friends/block")
@limiter.limit("10/minute")
async def block_user_endpoint(
    request: Request,
    req: FriendActionRequest,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        # 封鎖前先看我有沒有送出、還在等的邀請：封鎖會把它刪掉，對方的待處理數字要跟著收。
        # 沒有的話對方什麼都不用推——封鎖不該讓對方的畫面即時出現變化
        prior = await friends_repo.get_friendship_status(
            user_id, req.target_user_id, session=session
        )
        had_pending_from_me = bool(
            prior and prior["status"] == "pending" and prior["is_requester"]
        )
        result = await friends_repo.block_user(
            user_id,
            req.target_user_id,
            session=session,
        )

        if not result["success"]:
            error_messages = {
                "cannot_block_self": "You cannot block yourself",
            }
            raise HTTPException(
                status_code=400,
                detail=error_messages.get(result["error"], result["error"]),
            )

        # 封鎖會把兩人之間的邀請列一起刪掉：對方送我的邀請，鈴鐺裡那筆「接受／拒絕」鈕
        # 再按只會 400，一起收掉（同接受／拒絕／取消）；我送對方的同理，收在對方那邊
        await _clear_request_notifications(user_id, req.target_user_id, session)
        if had_pending_from_me:
            await _clear_request_notifications(req.target_user_id, user_id, session)
        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Block user failed: %s", e)
        raise HTTPException(status_code=500, detail="Block failed, please try again later")


@router.post("/api/friends/unblock")
@limiter.limit("10/minute")
async def unblock_user_endpoint(
    request: Request,
    req: FriendActionRequest,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        result = await friends_repo.unblock_user(
            user_id,
            req.target_user_id,
            session=session,
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail="This user is not blocked")

        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Unblock user failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to unblock, please try again later")


@router.get("/api/friends/blocked")
async def get_blocked_list(
    limit: int = Query(default=100, le=500),
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        blocked = await friends_repo.get_blocked_users(
            user_id, limit=limit, session=session
        )
        return {
            "success": True,
            "blocked_users": blocked,
            "count": len(blocked),
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Get blocked list failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch blocked list, please try again later")


# ============================================================================
# 好友列表
# ============================================================================


@router.get("/api/friends/list")
async def get_friends(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        friends = await friends_repo.get_friends_list(
            user_id,
            limit=limit,
            offset=offset,
            session=session,
        )
        count = await friends_repo.get_friends_count(user_id, session=session)

        return {
            "success": True,
            "friends": friends,
            "count": len(friends),
            "total": count,
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Get friends list failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch friend list, please try again later")


@router.get("/api/friends/requests/received")
async def get_received_requests(
    limit: int = Query(default=100, le=500),
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        requests = await friends_repo.get_pending_requests_received(
            user_id,
            limit=limit,
            session=session,
        )
        return {
            "success": True,
            "requests": requests,
            "count": len(requests),
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Get received requests failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch friend requests, please try again later")


@router.get("/api/friends/requests/sent")
async def get_sent_requests(
    limit: int = Query(default=100, le=500),
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        requests = await friends_repo.get_pending_requests_sent(
            user_id,
            limit=limit,
            session=session,
        )
        return {
            "success": True,
            "requests": requests,
            "count": len(requests),
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Get sent requests failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch sent requests, please try again later")


@router.get("/api/friends/status/{target_user_id}")
async def get_status(
    target_user_id: str,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        status = await friends_repo.get_friendship_status(
            user_id,
            target_user_id,
            session=session,
        )
        return {
            "success": True,
            "status": status.get("status") if status else None,
            "is_friend": status.get("status") == "accepted" if status else False,
            "is_requester": status.get("is_requester") if status else False,
            "blocked_by_me": status.get("blocked_by_me") if status else False,
            "details": status,
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Get friend status failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch status, please try again later")


@router.get("/api/friends/counts")
async def get_counts(
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        friends_count = await friends_repo.get_friends_count(user_id, session=session)
        pending_received = await friends_repo.get_pending_count(
            user_id, session=session
        )
        return {
            "success": True,
            "friends_count": friends_count,
            "pending_received": pending_received,
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Get friend counts failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch counts, please try again later")
