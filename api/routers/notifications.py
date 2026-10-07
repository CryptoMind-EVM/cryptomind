"""
通知 API 端點
"""

import asyncio
import json
import os
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, verify_token
from api.middleware.rate_limit import limiter
from api.utils import logger, run_sync
from core.database.notifications import (
    get_notifications as legacy_get_notifications,
)
from core.database.notifications import (
    get_unread_count as legacy_get_unread_count,
)
from core.nav_badges import compute_nav_badges
from core.orm.notifications_repo import notifications_repo
from core.orm.repositories import user_repo
from core.orm.session import get_async_session

router = APIRouter()


class NotificationConnectionManager:
    def __init__(self):
        self.active_connections: dict = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, user_id: str):
        async with self._lock:
            if user_id not in self.active_connections:
                self.active_connections[user_id] = set()
            self.active_connections[user_id].add(websocket)
        logger.info("User %s connected to notification WebSocket", user_id)

    async def disconnect(self, websocket: WebSocket, user_id: str):
        async with self._lock:
            if user_id in self.active_connections:
                self.active_connections[user_id].discard(websocket)
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]
        logger.info("User %s disconnected from notification WebSocket", user_id)

    async def send_to_user(self, user_id: str, data: dict):
        async with self._lock:
            connections = self.active_connections.get(user_id, set()).copy()
        for connection in connections:
            try:
                await connection.send_json(data)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error("Failed to send notification to user %s: %s", user_id, e)

    def is_user_online(self, user_id: str) -> bool:
        return (
            user_id in self.active_connections
            and len(self.active_connections[user_id]) > 0
        )


notification_manager = NotificationConnectionManager()


class CreateNotificationRequest(BaseModel):
    user_id: str = Field(..., description="接收通知的用戶 ID")
    type: str = Field(..., description="通知類型")
    title: str = Field(..., description="通知標題")
    body: str = Field(..., description="通知內容")
    data: Optional[dict] = Field(None, description="額外數據")


def _should_fallback_to_legacy_db(exc: Exception) -> bool:
    if isinstance(exc, ModuleNotFoundError):
        return True

    message = str(exc)
    return "async_generator" in message and "context manager" in message


# ============================================================================
# API 端點
# ============================================================================


@router.get("/api/notifications")
async def get_notifications_endpoint(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False, description="只返回未讀通知"),
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["user_id"]
        try:
            notifications = await notifications_repo.get_notifications(
                user_id=user_id,
                limit=limit,
                offset=offset,
                unread_only=unread_only,
            )
            unread_count = await notifications_repo.get_unread_count(user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            if not _should_fallback_to_legacy_db(exc):
                raise
            logger.warning(
                "Async notification store unavailable, falling back to legacy DB layer: %s",
                exc,
            )
            notifications = await run_sync(
                legacy_get_notifications, user_id, limit, offset, unread_only
            )
            unread_count = await run_sync(legacy_get_unread_count, user_id)

        return {
            "success": True,
            "notifications": notifications,
            "unread_count": unread_count,
            "count": len(notifications),
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Get notifications failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch notifications, please try again later",
        )


@router.get("/api/notifications/unread-count")
async def get_unread_count_endpoint(
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = current_user["user_id"]
        try:
            count = await notifications_repo.get_unread_count(user_id)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            if not _should_fallback_to_legacy_db(exc):
                raise
            logger.warning(
                "Async notification unread-count unavailable, falling back: %s",
                exc,
            )
            count = await run_sync(legacy_get_unread_count, user_id)
        return {"success": True, "count": count}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Get unread count failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch unread count, please try again later",
        )


@router.get("/api/notifications/badges")
@limiter.limit("60/minute")
async def get_nav_badges(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """功能選單的未讀標示：{social: 數字, forum: 有沒有}（規則見 core/nav_badges.py）"""
    try:
        return {"success": True, **await compute_nav_badges(current_user["user_id"])}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Get nav badges failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch badges")


@router.post("/api/notifications/{notification_id}/read")
@limiter.limit("30/minute")
async def mark_as_read_endpoint(
    request: Request,
    notification_id: str,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        success = await notifications_repo.mark_notification_as_read(
            notification_id,
            user_id,
            session=session,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Notification not found")

        return {"success": True, "message": "Marked as read"}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Mark as read failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Failed to mark as read, please try again later"
        )


@router.post("/api/notifications/read-all")
@limiter.limit("10/minute")
async def mark_all_as_read_endpoint(
    request: Request,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        count = await notifications_repo.mark_all_as_read(user_id, session=session)
        return {
            "success": True,
            "message": f"Marked {count} notification(s) as read",
            "count": count,
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Mark all as read failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Failed to mark all as read, please try again later"
        )


@router.delete("/api/notifications/{notification_id}")
@limiter.limit("20/minute")
async def delete_notification_endpoint(
    request: Request,
    notification_id: str,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_id = current_user["user_id"]
        success = await notifications_repo.delete_notification(
            notification_id,
            user_id,
            session=session,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Notification not found")

        return {"success": True, "message": "Notification deleted"}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Delete notification failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Failed to delete notification, please try again later",
        )


# ============================================================================
# WebSocket 端點
# ============================================================================


@router.websocket("/ws/notifications")
async def notification_websocket(websocket: WebSocket):
    user_id = None

    try:
        await websocket.accept()

        auth_message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
        auth_data = json.loads(auth_message)

        if auth_data.get("type") != "auth":
            await websocket.send_json(
                {"type": "error", "message": "Authentication required"}
            )
            await websocket.close(code=4001, reason="Authentication required")
            return

        token = auth_data.get("token") or auth_data.get("access_token")
        if not token:
            # 前端不再持有 JWT；同源 WebSocket 握手會帶 httpOnly cookie
            from api.deps import ACCESS_TOKEN_COOKIE

            token = websocket.cookies.get(ACCESS_TOKEN_COOKIE)
        if not token:
            await websocket.send_json(
                {"type": "error", "message": "JWT Token required"}
            )
            await websocket.close(code=4002, reason="JWT Token required")
            return

        if os.getenv("TEST_MODE", "").lower() == "true" and token.startswith("test-"):
            user_id = token
            logger.info("Notification WebSocket Dev Auth: %s", user_id)
        else:
            try:
                payload = verify_token(token)
                user_id = payload.get("sub")
                if not user_id:
                    raise HTTPException(status_code=401, detail="Invalid token payload")
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning("Notification WebSocket auth failed: %s", e)
                await websocket.send_json({"type": "error", "message": "Invalid Token"})
                await websocket.close(code=4003, reason="Invalid Token")
                return

        claimed_user_id = auth_data.get("user_id")
        if claimed_user_id and claimed_user_id != user_id:
            logger.warning(
                "Notification WebSocket auth mismatch: Token user %s != Claimed %s",
                user_id,
                claimed_user_id,
            )
            await websocket.send_json({"type": "error", "message": "User ID mismatch"})
            await websocket.close(code=4004, reason="User ID mismatch")
            return

        user_exists = await user_repo.get_by_id(user_id)
        if not user_exists:
            await websocket.send_json({"type": "error", "message": "User not found"})
            await websocket.close(code=4005, reason="User not found")
            return

        await notification_manager.connect(websocket, user_id)
        logger.info(
            "User %s authenticated successfully via notification WebSocket", user_id
        )

        await websocket.send_json(
            {
                "type": "connected",
                "message": "Connected to notification service",
                "user_id": user_id,
            }
        )

        # GAP-3: 背景 token 重驗。token 過期/revoke 時主動 close(4401)。
        from api.routers.ws_auth import start_reauth_watcher

        reauth_task = await start_reauth_watcher(websocket, token)

        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                continue

    except asyncio.TimeoutError:
        await websocket.close(code=4003, reason="Authentication timeout")
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for user %s", user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Notification WebSocket error: %s", e)
    finally:
        try:
            reauth_task.cancel()
        except NameError:
            pass  # reauth_task 可能未建立(握手前失敗)
        if user_id:
            await notification_manager.disconnect(websocket, user_id)


async def push_notification_to_user(user_id: str, notification: dict):
    await notification_manager.send_to_user(
        user_id,
        {
            "type": "notification",
            "data": notification,
        },
    )


async def push_notification_updated(user_id: str, notification: dict):
    """通知內容被改了（例如私訊收回）→ 開著的鈴鐺原地換掉，不跳 toast。"""
    await notification_manager.send_to_user(
        user_id, {"type": "notification_updated", "data": notification}
    )


async def push_notifications_read(user_id: str, notification_ids: list[str]):
    """別的分頁／裝置讀掉了 → 讓每個開著的鈴鐺同步變已讀。"""
    if not notification_ids:
        return
    await notification_manager.send_to_user(
        user_id,
        {"type": "notifications_read", "data": {"ids": notification_ids}},
    )


async def push_badges_changed(user_id: str):
    """側欄數字（好友邀請等）變了、但沒有通知可推（通知早已讀，例如對方撤回邀請）→
    叫每個開著的頁面重抓徽章與邀請列表。"""
    await notification_manager.send_to_user(user_id, {"type": "badges_changed"})
