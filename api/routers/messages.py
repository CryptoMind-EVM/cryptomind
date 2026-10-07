"""
私訊功能 API 端點
"""

import asyncio
import json
import os
import time
from typing import Callable, Dict, Literal, Optional, Set

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field, model_validator

from api.deps import get_current_user, verify_token
from api.middleware.rate_limit import limiter
from api.routers.notifications import (
    push_notification_to_user,
    push_notification_updated,
    push_notifications_read,
)
from api.utils import logger, run_sync
from core.ai_card import CARD_TYPE, card_preview
from core.database import (
    check_and_increment_greeting,
    check_and_increment_message,
    check_greeting_limit,
    check_message_limit,
    get_conversation_with_messages,
    get_user_by_id,
    get_user_membership,
    hide_conversation_for_user,
    hide_dm_message_for_user,
    is_blocked,
    search_messages,
    send_greeting,
    update_last_active,
)
from core.moderation.reports import dm_report_text, score_dm_report, spawn
from core.orm.chat_assistant_history_repo import chat_assistant_history_repo
from core.orm.dm_reports_repo import dm_reports_repo
from core.orm.messages_repo import messages_repo
from core.orm.notifications_repo import notifications_repo
from core.orm.repositories import user_repo

router = APIRouter()


# ============================================================================
# 請求模型
# ============================================================================


class SendMessageRequest(BaseModel):
    to_user_id: str = Field(..., description="接收者用戶 ID")
    content: str = Field("", max_length=2000, description="訊息內容")
    reply_to_message_id: Optional[int] = Field(
        None, ge=1, description="回覆哪一則（同一對話、未收回）"
    )
    assistant_turn_id: Optional[int] = Field(
        None,
        ge=1,
        description="分享 AI 助理的回答成卡片（ai_card）：內容由伺服器依這個 id 取，content 不用帶也不採用",
    )

    @model_validator(mode="after")
    def _content_or_card(self):
        if self.assistant_turn_id is None and not self.content:
            raise ValueError("content is required")
        return self


class ReactionRequest(BaseModel):
    # 同 core/dm_reactions.REACTION_KEYS（前端 SVG 的 key）
    reaction: Literal["like", "love", "haha", "wow", "sad", "rocket", "diamond", "ok"]


class ReportRequest(BaseModel):
    reason: Literal["scam", "harassment", "spam", "other"]
    note: Optional[str] = Field(None, max_length=500, description="補充說明（選填）")
    block: bool = Field(False, description="同時封鎖此人")


class MarkReadRequest(BaseModel):
    conversation_id: int = Field(..., description="對話 ID")


# ============================================================================
# WebSocket 連接管理器
# ============================================================================


class MessageConnectionManager:
    """管理 WebSocket 連接"""

    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def register(self, websocket: WebSocket, user_id: str):
        """認證通過後登記；同一人可以多條（手機＋電腦＋多分頁都收得到）。"""
        async with self.lock:
            self.active_connections.setdefault(user_id, set()).add(websocket)

    async def disconnect(self, websocket: WebSocket, user_id: str):
        async with self.lock:
            if user_id in self.active_connections:
                self.active_connections[user_id].discard(websocket)
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]
        logger.info(f"用戶 {user_id} WebSocket 斷開")

    async def send_to_user(self, user_id: str, data: dict):
        """發送訊息給特定用戶的所有連接"""
        async with self.lock:
            connections = self.active_connections.get(user_id, set()).copy()

        dead = []
        for connection in connections:
            try:
                await connection.send_json(data)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error(f"發送訊息給用戶 {user_id} 失敗: {e}")
                dead.append(connection)

        if dead:
            async with self.lock:
                conns = self.active_connections.get(user_id)
                if conns:
                    conns -= set(dead)
                    if not conns:
                        del self.active_connections[user_id]


message_manager = MessageConnectionManager()


# ============================================================================
# API 端點
# ============================================================================


@limiter.limit("30/minute")
@router.get("/api/messages/conversations")
async def get_conversations_endpoint(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    # 排序模式存在前端；custom＝自訂順序（c068，置頂仍在最前面），分頁照同一個順序切
    order: Literal["recent", "custom"] = Query("recent"),
    current_user: dict = Depends(get_current_user),
):
    """
    取得對話列表
    """
    try:
        user_id = current_user["user_id"]
        conversations = await messages_repo.get_conversations(
            user_id, limit=limit, offset=offset, order=order
        )
        total_unread = await messages_repo.get_unread_count(user_id)

        return {
            "success": True,
            "conversations": conversations,
            "count": len(conversations),
            "total_unread": total_unread,
        }
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"取得對話列表失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch conversation list, please try again later")


@limiter.limit("30/minute")
@router.get("/api/messages/conversation/{conversation_id}")
async def get_messages_endpoint(
    request: Request,
    conversation_id: int,
    limit: int = Query(50, ge=1, le=100),
    before_id: Optional[int] = Query(None, description="取得此 ID 之前的訊息"),
    current_user: dict = Depends(get_current_user),
):
    """
    取得對話中的訊息
    """
    try:
        user_id = current_user["user_id"]
        result = await messages_repo.get_messages(
            conversation_id, user_id, limit=limit, before_id=before_id
        )

        if not result["success"]:
            raise HTTPException(status_code=404, detail="Conversation not found or access denied")

        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"取得訊息失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch messages, please try again later")


@limiter.limit("30/minute")
@router.get("/api/messages/with/{other_user_id}")
async def get_conversation_with_user_endpoint(
    request: Request,
    other_user_id: str,
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """
    取得與特定用戶的對話和訊息（優化版：單一數據庫連接）
    """
    try:
        user_id = current_user["user_id"]
        result = await run_sync(
            get_conversation_with_messages, user_id, other_user_id, limit
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=500, detail=result.get("error", "Failed to fetch conversation")
            )

        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"取得對話失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch conversation, please try again later")


@router.post("/api/messages/send")
@limiter.limit("30/minute")
async def send_message_endpoint(
    request: Request,
    body: SendMessageRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    發送訊息（僅限好友）- 優化版本
    """
    try:
        user_id = current_user["user_id"]

        validation = await messages_repo.validate_message_send(user_id, body.to_user_id)

        if not validation["valid"]:
            error = validation["error"]
            if error == "sender_not_found":
                raise HTTPException(status_code=401, detail="User not found")
            elif error == "receiver_not_found":
                raise HTTPException(status_code=404, detail="Recipient not found")
            elif error == "blocked":
                raise HTTPException(status_code=403, detail="Cannot send messages to this user")
            elif error == "not_friends":
                raise HTTPException(status_code=403, detail="You can only send messages to friends")
            else:
                raise HTTPException(status_code=400, detail="Validation failed")

        # 回覆目標要在扣今日額度之前驗：不合法（已收回、不在這個對話）不能白扣一則
        if body.reply_to_message_id is not None:
            reply_error = await messages_repo.check_reply_target(
                user_id, body.to_user_id, body.reply_to_message_id
            )
            if reply_error:
                status = 404 if reply_error == "reply_target_not_found" else 400
                raise HTTPException(status_code=status, detail=reply_error)

        # AI 分析卡片：內容由伺服器取（自己問的、這個對話、沒過期），也在扣額度前驗
        content, card = body.content, body.assistant_turn_id is not None
        if card:
            conversation_id = await chat_assistant_history_repo.dm_conversation_id(
                user_id, body.to_user_id
            )
            turn = (
                await chat_assistant_history_repo.get_turn_for_share(
                    "dm", conversation_id, user_id, body.assistant_turn_id
                )
                if conversation_id is not None
                else None
            )
            if turn is None:
                raise HTTPException(status_code=404, detail="not_found")
            content = turn["answer"]

        membership = await run_sync(get_user_membership, user_id)
        is_premium = membership.get("is_premium", False)
        limit_check = await run_sync(
            lambda: check_and_increment_message(user_id, is_premium)
        )

        if not limit_check["can_send"]:
            raise HTTPException(
                status_code=429,
                detail=f"Daily message limit reached ({limit_check['limit']}). Upgrade to Premium for unlimited messaging",
            )

        result = await messages_repo.send_message(
            user_id,
            body.to_user_id,
            content,
            reply_to_message_id=body.reply_to_message_id,
            **({"message_type": CARD_TYPE} if card else {}),
        )

        if not result["success"]:
            error = result.get("error", "Failed to send message")
            # 回覆的那則不存在／不在這個對話：404；已收回：400（前端比對字串顯示在地化文案）
            status = 404 if error == "reply_target_not_found" else 400
            raise HTTPException(status_code=status, detail=error)

        await message_manager.send_to_user(
            body.to_user_id, {"type": "new_message", "message": result["message"]}
        )

        await message_manager.send_to_user(
            user_id, {"type": "message_sent", "message": result["message"]}
        )

        try:
            msg = result["message"]
            # 卡片整篇不放進通知，只給一行預覽
            preview = card_preview(msg["content"]) if card else msg["content"]
            notification = await notifications_repo.notify_new_message(
                body.to_user_id,
                user_id,
                msg.get("from_display_name") or msg.get("from_username", user_id),
                preview,
                str(msg["conversation_id"]),
                message_id=msg["id"],
            )
            if notification:
                await push_notification_to_user(body.to_user_id, notification)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as notify_error:
            logger.warning(f"Failed to send message notification: {notify_error}")

        # 今日額度一起帶回：前端輸入列下的「今日還能傳 N 則」不用每則再打一次 /limits
        result["message_limit"] = limit_check
        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"發送訊息失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to send message, please try again later")


@router.post("/api/messages/read")
@limiter.limit("30/minute")
async def mark_read_endpoint(
    request: Request,
    req: MarkReadRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    標記對話為已讀
    """
    try:
        user_id = current_user["user_id"]
        result = await messages_repo.mark_as_read(req.conversation_id, user_id)

        if not result["success"]:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # 讀了對話，鈴鐺裡這個對話的通知一起清掉（其他分頁／裝置同步）
        try:
            cleared = await notifications_repo.mark_message_notifications_read(
                user_id, req.conversation_id
            )
            await push_notifications_read(user_id, cleared)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as notify_error:
            logger.warning(f"Failed to clear message notifications: {notify_error}")

        conv = await messages_repo.get_conversation_by_id(req.conversation_id, user_id)
        if conv:
            other_user_id = (
                conv["user2_id"] if conv["user1_id"] == user_id else conv["user1_id"]
            )

            other_membership = await run_sync(get_user_membership, other_user_id)
            is_other_premium = other_membership.get("is_premium", False)
            if is_other_premium:
                await message_manager.send_to_user(
                    other_user_id,
                    {
                        "type": "read_receipt",
                        "conversation_id": req.conversation_id,
                        "read_by": user_id,
                    },
                )

        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"標記已讀失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to mark as read, please try again later")


@router.post("/api/messages/greeting")
@limiter.limit("5/minute")
async def send_greeting_endpoint(
    request: Request,
    body: SendMessageRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    發送打招呼訊息（Premium 會員專屬，可發給非好友）
    """
    try:
        user_id = current_user["user_id"]
        sender_exists = await run_sync(get_user_by_id, user_id)
        if not sender_exists:
            raise HTTPException(status_code=401, detail="User not found")

        receiver_exists = await run_sync(get_user_by_id, body.to_user_id)
        if not receiver_exists:
            raise HTTPException(status_code=404, detail="Recipient not found")

        membership = await run_sync(get_user_membership, user_id)
        if not membership.get("is_premium", False):
            raise HTTPException(
                status_code=403, detail="Greetings are only available to Premium members"
            )

        blocked = await run_sync(is_blocked, user_id, body.to_user_id)
        if blocked:
            raise HTTPException(status_code=403, detail="Cannot send messages to this user")

        is_premium = membership.get("is_premium", False)
        limit_check = await run_sync(
            lambda: check_and_increment_greeting(user_id, is_premium)
        )
        if not limit_check["can_send"]:
            raise HTTPException(
                status_code=429,
                detail=f"Monthly greeting limit reached ({limit_check['limit']})",
            )

        result = await run_sync(send_greeting, user_id, body.to_user_id, body.content)

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result.get("error", "Failed to send message"))

        await message_manager.send_to_user(
            body.to_user_id, {"type": "new_message", "message": result["message"]}
        )

        try:
            msg = result["message"]
            notification = await notifications_repo.notify_new_message(
                body.to_user_id,
                user_id,
                msg.get("from_display_name") or msg.get("from_username", user_id),
                msg["content"],
                str(msg["conversation_id"]),
                message_id=msg["id"],
            )
            if notification:
                await push_notification_to_user(body.to_user_id, notification)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as notify_error:
            logger.warning(f"Failed to send greeting notification: {notify_error}")

        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"發送打招呼失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to send greeting, please try again later")


@limiter.limit("30/minute")
@router.get("/api/messages/search")
async def search_messages_endpoint(
    request: Request,
    q: str = Query(..., min_length=1, max_length=100, description="搜尋關鍵字"),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """
    搜尋訊息（Premium 會員專屬）
    """
    try:
        user_id = current_user["user_id"]
        membership = await run_sync(get_user_membership, user_id)
        if not membership.get("is_premium", False):
            raise HTTPException(
                status_code=403, detail="Message search is only available to Premium members"
            )

        results = await run_sync(lambda: search_messages(user_id, q, limit=limit))

        return {"success": True, "results": results, "count": len(results)}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"搜尋訊息失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to search messages, please try again later")


@limiter.limit("30/minute")
@router.get("/api/messages/limits")
async def get_message_limits_endpoint(request: Request, current_user: dict = Depends(get_current_user)):
    """
    取得用戶的訊息限制狀態
    """
    try:
        user_id = current_user["user_id"]
        from core.orm.config_repo import config_repo

        membership = await run_sync(get_user_membership, user_id)
        is_premium = membership.get("is_premium", False)

        message_limit = await run_sync(lambda: check_message_limit(user_id, is_premium))
        greeting_limit = await run_sync(
            lambda: check_greeting_limit(user_id, is_premium)
        )
        max_length = await config_repo.get_config("limit_message_max_length", 500)
        return {
            "success": True,
            "is_premium": is_premium,
            "message_limit": message_limit,
            "greeting_limit": greeting_limit,
            "max_length": max_length,
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"取得限制狀態失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch limit status, please try again later")


@router.delete("/api/messages/{message_id}")
@limiter.limit("20/minute")
async def delete_message_endpoint(
    request: Request, message_id: int, current_user: dict = Depends(get_current_user)
):
    """
    刪除訊息
    """
    try:
        user_id = current_user["user_id"]
        result = await messages_repo.recall_message(message_id, user_id)

        if not result["success"]:
            error = result.get("error", "")
            if error == "message_not_found":
                raise HTTPException(status_code=404, detail="Message not found")
            elif error == "permission_denied":
                raise HTTPException(status_code=403, detail="You do not have permission to delete this message")
            elif error == "recall_window_expired":
                # 前端比對這個字串換成在地化文案
                raise HTTPException(status_code=403, detail="recall_window_expired")
            elif error == "already_recalled":
                # 別的裝置先收回了：前端當成功處理
                raise HTTPException(status_code=409, detail="already_recalled")
            else:
                raise HTTPException(status_code=400, detail=f"Failed to delete: {error}")

        # 雙方畫面即時換成「已收回」；對方鈴鐺裡那則預覽原地改掉
        event = {
            "type": "message_recalled",
            "message_id": result["message_id"],
            "conversation_id": result["conversation_id"],
        }
        for uid in (result["from_user_id"], result["to_user_id"]):
            await message_manager.send_to_user(uid, event)
        for notification in result["notifications"]:
            await push_notification_updated(result["to_user_id"], notification)

        return {"success": True, "recalled": True}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"刪除訊息失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete message, please try again later")


async def _forget_assistant(coro) -> None:
    """隱藏／刪對話已經成功了：清 AI 助理問答失敗只記 log，不要讓使用者看到「刪除失敗」"""
    try:
        await coro
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"清除 AI 助理問答失敗: {e}")


@router.post("/api/messages/{message_id}/hide")
@limiter.limit("20/minute")
async def hide_message_endpoint(
    request: Request, message_id: int, current_user: dict = Depends(get_current_user)
):
    """
    隱藏訊息（只對自己隱藏，不影響對方）
    類似 WhatsApp 的「為我刪除」功能
    """
    try:
        user_id = current_user["user_id"]
        result = await run_sync(hide_dm_message_for_user, message_id, user_id)

        if not result["success"]:
            error = result.get("error", "")
            if error == "message_not_found":
                raise HTTPException(status_code=404, detail="Message not found")
            else:
                raise HTTPException(status_code=400, detail=f"Failed to hide: {error}")

        # 自己刪掉的訊息，AI 助理整理過它的問答也不留
        await _forget_assistant(
            chat_assistant_history_repo.invalidate_message(
                "dm", message_id, user_id=user_id
            )
        )
        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"隱藏訊息失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to hide message, please try again later")


async def _push_reactions(result: dict, message_id: int) -> None:
    """表情變了：雙方畫面（含自己別的裝置）即時更新那則的表情膠囊"""
    event = {
        "type": "reaction_updated",
        "message_id": message_id,
        "conversation_id": result["conversation_id"],
        "reactions": result["reactions"],
    }
    for uid in result["participants"]:
        await message_manager.send_to_user(uid, event)


def _reaction_error(error: str) -> HTTPException:
    if error == "message_not_found":
        return HTTPException(status_code=404, detail="Message not found")
    if error == "blocked":
        return HTTPException(status_code=403, detail="blocked")
    # message_recalled／invalid_reaction：前端比對字串
    return HTTPException(status_code=400, detail=error)


@router.put("/api/messages/{message_id}/reaction")
@limiter.limit("60/minute")
async def set_reaction_endpoint(
    request: Request,
    message_id: int,
    body: ReactionRequest,
    current_user: dict = Depends(get_current_user),
):
    """按表情（每人每則一個，按別的就替換）"""
    try:
        result = await messages_repo.set_reaction(
            message_id, current_user["user_id"], body.reaction
        )
        if not result["success"]:
            raise _reaction_error(result.get("error", ""))
        await _push_reactions(result, message_id)
        return {"success": True, "reactions": result["reactions"]}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"按表情失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to react, please try again later")


@router.delete("/api/messages/{message_id}/reaction")
@limiter.limit("60/minute")
async def remove_reaction_endpoint(
    request: Request, message_id: int, current_user: dict = Depends(get_current_user)
):
    """收回自己的表情"""
    try:
        result = await messages_repo.remove_reaction(message_id, current_user["user_id"])
        if not result["success"]:
            raise _reaction_error(result.get("error", ""))
        if result.get("changed"):  # 本來就沒按：不推，免得連打 DELETE 洗對方畫面
            await _push_reactions(result, message_id)
        return {"success": True, "reactions": result["reactions"]}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"收回表情失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove reaction, please try again later")


@router.post("/api/messages/{message_id}/report")
@limiter.limit("10/hour")
async def report_message_endpoint(
    request: Request,
    message_id: int,
    body: ReportRequest,
    current_user: dict = Depends(get_current_user),
):
    """檢舉對方的一則訊息：後端存那則＋前 10 則的快照（收回不影響證據），可順便封鎖"""
    try:
        result = await dm_reports_repo.report_message(
            message_id,
            current_user["user_id"],
            body.reason,
            (body.note or "").strip() or None,
            block=body.block,
        )
        if not result["success"]:
            error = result.get("error", "")
            if error == "message_not_found":
                raise HTTPException(status_code=404, detail="Message not found")
            if error == "already_reported":
                raise HTTPException(status_code=409, detail="already_reported")
            # cannot_report_own 等：前端比對字串
            raise HTTPException(status_code=400, detail=error)
        # 背景替這筆檢舉打風險分數（後台照危險程度排）：只看被檢舉的人最近傳的幾則
        report = result.get("report") or {}
        if report.get("id") and report.get("reported_user_id"):
            spawn(
                score_dm_report(
                    report["id"],
                    dm_report_text(report.get("snapshot") or [], report["reported_user_id"]),
                )
            )
        return {"success": True, "blocked": result["blocked"]}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"檢舉訊息失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to report, please try again later")


@router.delete("/api/conversations/{conversation_id}")
@limiter.limit("10/minute")
async def delete_conversation_endpoint(
    request: Request,
    conversation_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    刪除對話（隱藏整段對話，只對自己隱藏）
    類似 WhatsApp 的「刪除對話」功能
    """
    try:
        user_id = current_user["user_id"]
        result = await run_sync(hide_conversation_for_user, conversation_id, user_id)

        logger.info(f"刪除對話結果: {result}")

        if not result["success"]:
            error = result.get("error", "")
            if error == "conversation_not_found":
                raise HTTPException(status_code=404, detail="Conversation not found")
            else:
                raise HTTPException(status_code=400, detail=f"Failed to delete conversation: {error}")

        await _forget_assistant(
            chat_assistant_history_repo.invalidate_chat(
                "dm", conversation_id, user_id=user_id
            )
        )
        return result
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"刪除對話失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete conversation, please try again later")


# ============================================================================
# 「輸入中」轉送（短暫事件，不存 DB）
# ============================================================================

_TYPING_MIN_INTERVAL = 1.0  # 同對話 start 的最小間隔（前端約 3 秒送一次）
_TYPING_ANY_INTERVAL = 0.2  # 同一連線任何 typing 事件的最小間隔（擋灌 DB 查詢）
_TYPING_AUTH_TTL = 60.0  # 權限快取：封鎖／解除好友最慢 60 秒生效
_TYPING_MAX_TARGETS = 100


async def _relay_typing(
    user_id: str,
    username: str,
    message: dict,
    state: dict,
    now: Callable[[], float] = time.monotonic,
) -> None:
    """把「輸入中」轉給同對話、仍可互傳訊息的另一方。

    權限比照送訊：必須是對話成員，且 validate_message_send 通過（好友、沒封鎖）。
    state 是這條 WS 連線自己的 dict（權限快取、上次事件時間）。
    """
    conversation_id = message.get("conversation_id")
    if type(conversation_id) is not int:  # bool 是 int 的子類，要擋
        return
    t = now()
    targets = state.setdefault("targets", {})
    last_start = state.setdefault("last_start", {})

    if message.get("state") == "stop":
        # 只轉給剛 start 過、權限還在快取裡的對象：不查 DB，也不受節流
        # （打一個字就送出，stop 被吞掉對方的提示會卡 6 秒）
        cached = targets.get(conversation_id)
        if conversation_id not in last_start or not cached or not cached[0] or t >= cached[1]:
            return
        del last_start[conversation_id]
        await _send_typing(cached[0], conversation_id, user_id, username, "stop")
        return

    last_any = state.get("last_any")
    if last_any is not None and t - last_any < _TYPING_ANY_INTERVAL:
        return
    prev = last_start.get(conversation_id)
    if prev is not None and t - prev < _TYPING_MIN_INTERVAL:
        return
    state["last_any"] = t

    cached = targets.get(conversation_id)
    if cached and t < cached[1]:
        other_user_id = cached[0]
    else:
        other_user_id = None
        conv = await messages_repo.get_conversation_by_id(conversation_id, user_id)
        if conv:
            candidate = (
                conv["user2_id"] if conv["user1_id"] == user_id else conv["user1_id"]
            )
            validation = await messages_repo.validate_message_send(user_id, candidate)
            if validation.get("valid"):
                other_user_id = candidate
        targets.pop(conversation_id, None)
        if len(targets) >= _TYPING_MAX_TARGETS:
            del targets[next(iter(targets))]  # 淘汰最舊一筆，不整包清掉
        targets[conversation_id] = (other_user_id, t + _TYPING_AUTH_TTL)
    if not other_user_id:
        return

    last_start[conversation_id] = t
    await _send_typing(other_user_id, conversation_id, user_id, username, "start")


async def _send_typing(
    to_user_id: str, conversation_id: int, from_user_id: str, username: str, typing_state: str
) -> None:
    await message_manager.send_to_user(
        to_user_id,
        {
            "type": "typing",
            "conversation_id": conversation_id,
            "from_user_id": from_user_id,
            "from_username": username,
            "state": typing_state,
        },
    )


# ============================================================================
# WebSocket 端點
# ============================================================================


@router.websocket("/ws/messages")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket 端點 - 即時訊息推送

    客戶端訊息格式:
    {"action": "auth", "user_id": "xxx"}  - 認證
    {"action": "ping"}                     - 心跳
    {"action": "typing", "conversation_id": 1, "state": "start"|"stop"} - 輸入中
    """
    user_id = None

    try:
        await websocket.accept()

        try:
            auth_data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            auth_message = json.loads(auth_data)

            token = auth_message.get("token") or auth_message.get("access_token")

            if not token:
                from api.deps import ACCESS_TOKEN_COOKIE

                token = websocket.cookies.get(ACCESS_TOKEN_COOKIE)

            if not token:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Authentication required (Missing Token)",
                    }
                )
                await websocket.close()
                return

            if os.getenv("TEST_MODE", "").lower() == "true" and token.startswith(
                "test-"
            ):
                user_id = token
                logger.info(f"WebSocket Dev Auth: {user_id}")
            else:
                try:
                    payload = verify_token(token)
                    user_id = payload.get("sub")
                    if not user_id:
                        raise HTTPException(
                            status_code=401, detail="Invalid token payload"
                        )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:
                    logger.warning(f"WebSocket auth failed: {e}")
                    await websocket.send_json(
                        {"type": "error", "message": "Invalid Token"}
                    )
                    await websocket.close()
                    return

            if auth_message.get("user_id") and auth_message["user_id"] != user_id:
                logger.warning(
                    f"WebSocket auth mismatch: Token user {user_id} != Claimed {auth_message['user_id']}"
                )
                await websocket.send_json(
                    {"type": "error", "message": "User ID mismatch"}
                )
                await websocket.close()
                return

            user_exists = await run_sync(get_user_by_id, user_id)
            if not user_exists:
                await websocket.send_json({"type": "error", "message": "User not found"})
                await websocket.close()
                return

        except asyncio.TimeoutError:
            await websocket.send_json({"type": "error", "message": "Authentication timeout"})
            await websocket.close()
            return

        await message_manager.register(websocket, user_id)

        logger.info(f"用戶 {user_id} WebSocket 認證成功")

        await run_sync(update_last_active, user_id)

        unread_count = await messages_repo.get_unread_count(user_id)
        await websocket.send_json(
            {"type": "authenticated", "user_id": user_id, "unread_count": unread_count}
        )

        # GAP-3: 背景 token 重驗。token 過期/revoke 時主動 close(4401)。
        from api.routers.ws_auth import start_reauth_watcher

        reauth_task = await start_reauth_watcher(websocket, token)

        # 輸入中顯示暱稱（沒設才用帳號名）
        username = (
            await user_repo.get_display_name(user_id)
            or user_exists.get("username")
            or user_id
        )
        typing_state: dict = {}

        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            if not isinstance(message, dict):
                continue
            action = message.get("action")

            if action == "ping":
                await websocket.send_json({"type": "pong"})
            elif action == "typing":
                if "group_id" in message:
                    # 群組的「輸入中」（router 在 group_chat.py；這裡延後 import，避免循環）
                    from api.routers.group_chat import relay_group_typing

                    await relay_group_typing(user_id, username, message, typing_state)
                else:
                    await _relay_typing(user_id, username, message, typing_state)

    except WebSocketDisconnect:
        logger.info(f"用戶 {user_id} WebSocket 主動斷開")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"WebSocket 錯誤: {e}")
    finally:
        try:
            reauth_task.cancel()
        except NameError:
            pass  # reauth_task 可能未建立(握手前失敗)
        if user_id:
            await message_manager.disconnect(websocket, user_id)
