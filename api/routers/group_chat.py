"""
群組聊天 API（c066，設計 docs/plans/2026-10-01-group-chat-design.md）

- 功能開關 system_config.group_chat_enabled 關著時整組 404（router 層 dependency）。
- 非成員一律 404（不透露群組存不存在）。
- 寫入類操作（開群、送訊息、按表情、邀請、接受邀請、改群、踢人、轉讓群主）要 Pro：Pro 到期的人
  留在群裡唯讀（還是能讀、退群、關通知、收回自己的訊息、檢舉；群主還能解散）。
- 即時推播沿用 /ws/messages 的 message_manager（行程內連線表，單一 worker 才完整；
  改多 worker 時私訊與群組要一起改走 Redis）。
- rate limit：@router 在上、@limiter 在下（反過來 limit 不會生效）。
"""

import asyncio
import time
from typing import Callable, Iterable, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.routers.messages import message_manager
from api.routers.notifications import (
    push_notification_to_user,
    push_notification_updated,
    push_notifications_read,
)
from api.utils import logger, run_sync
from core.ai_card import CARD_TYPE, card_preview
from core.database import get_user_by_id, get_user_membership
from core.moderation.reports import dm_report_text, score_group_report, spawn
from core.orm.chat_assistant_history_repo import chat_assistant_history_repo
from core.orm.config_repo import config_repo
from core.orm.group_chat_repo import group_chat_repo
from core.orm.group_messages_repo import group_messages_repo
from core.orm.notifications_repo import notifications_repo
from core.orm.repositories import user_repo


async def require_group_chat_enabled() -> None:
    """開關沒開＝這組 API 不存在"""
    if not await config_repo.get_config("group_chat_enabled", False):
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(dependencies=[Depends(require_group_chat_enabled)])


# ============================================================================
# 請求模型
# ============================================================================


class CreateGroupRequest(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=30
    )  # 同 group_chat_repo.NAME_MAX、DB CHECK
    invitee_ids: List[str] = Field(default_factory=list, max_length=50)


class UpdateGroupRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=30)
    history_visible: Optional[bool] = None


class MuteRequest(BaseModel):
    muted: bool


class TransferOwnerRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)


class InviteRequest(BaseModel):
    user_ids: List[str] = Field(..., min_length=1, max_length=50)


class SendGroupMessageRequest(BaseModel):
    content: str = Field("", max_length=2000)
    reply_to_message_id: Optional[int] = Field(None, ge=1)
    # 分享 AI 助理的回答成卡片（ai_card）：內容由伺服器依這個 id 取，content 不用帶也不採用
    assistant_turn_id: Optional[int] = Field(None, ge=1)

    @model_validator(mode="after")
    def _content_or_card(self):
        if self.assistant_turn_id is None and not self.content:
            raise ValueError("content is required")
        return self


class MarkGroupReadRequest(BaseModel):
    last_message_id: int = Field(..., ge=0)


class GroupReactionRequest(BaseModel):
    # 同 core/dm_reactions.REACTION_KEYS
    reaction: Literal["like", "love", "haha", "wow", "sad", "rocket", "diamond", "ok"]


class GroupReportRequest(BaseModel):
    reason: Literal["scam", "harassment", "spam", "other"]
    note: Optional[str] = Field(None, max_length=500)


# ============================================================================
# 共用
# ============================================================================

_STATUS = {
    "not_found": 404,
    "message_not_found": 404,
    "invite_not_found": 404,
    "target_not_member": 404,
    "reply_target_not_found": 404,
    "not_owner": 403,
    "permission_denied": 403,
    "not_premium": 403,
    "recall_window_expired": 403,
    "group_full": 409,
    "target_not_premium": 409,
    "create_limit_reached": 409,
    "join_limit_reached": 409,
    "already_reported": 409,
    "already_recalled": 409,
    "daily_invite_limit_reached": 429,
}


def _raise(error: str) -> None:
    """repo 錯誤碼 → HTTP；detail 就是錯誤碼（前端比對字串顯示在地化文案）"""
    raise HTTPException(
        status_code=_STATUS.get(error, 400), detail=error or "bad_request"
    )


async def _require_pro(user_id: str) -> None:
    membership = await run_sync(get_user_membership, user_id)
    if not membership.get("is_premium", False):
        raise HTTPException(status_code=403, detail="pro_required")


async def _broadcast(user_ids: Iterable[str], payload: dict) -> None:
    for uid in user_ids:
        await message_manager.send_to_user(uid, payload)


async def _broadcast_system(
    group_id: int, member_ids: List[str], messages: List[dict], kind: str
) -> None:
    """系統訊息（加入、退出、改名…）照一般訊息推，再推一個 group_updated 讓列表／資訊面板重抓"""
    for message in messages:
        await _broadcast(
            member_ids,
            {"type": "group_message", "group_id": group_id, "message": message},
        )
    await _broadcast(
        member_ids, {"type": "group_updated", "group_id": group_id, "kind": kind}
    )


async def _safe_notify(coro) -> Optional[dict]:
    """通知失敗不影響主操作（同私訊）"""
    try:
        return await coro
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"群組通知失敗: {e}")
        return None


async def _clear_notifications(user_id: str, ids: List[str]) -> None:
    if ids:
        await push_notifications_read(user_id, ids)


async def _send_invites(group_id: int, inviter_id: str, invitee_ids: List[str]) -> dict:
    result = await group_chat_repo.create_invites(group_id, inviter_id, invitee_ids)
    if not result["success"]:
        return result
    inviter = await _display_name(inviter_id)
    for item in result["invited"]:
        notification = await _safe_notify(
            notifications_repo.create_notification(
                user_id=item["invitee_id"],
                notification_type="group_invite",
                title="群組邀請",
                body=f"{inviter} 邀請你加入「{result['group_name']}」",
                data={
                    "invite_id": item["invite_id"],
                    "group_id": group_id,
                    "group_name": result["group_name"],
                    "inviter_id": inviter_id,
                    "inviter_name": inviter,
                },
            )
        )
        if notification:
            await push_notification_to_user(item["invitee_id"], notification)
    return result


async def _display_name(user_id: str) -> str:
    """暱稱優先，沒設才用帳號名（同 #946 的顯示規則）"""
    name = await user_repo.get_display_name(user_id)
    if name:
        return name
    user = await run_sync(get_user_by_id, user_id) or {}
    return user.get("username") or user_id


# ============================================================================
# 群組
# ============================================================================


@router.get("/api/groups")
@limiter.limit("60/minute")
async def list_groups(request: Request, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    groups = await group_chat_repo.list_groups(user_id)
    # 「有人提及你」：還沒讀的群組通知標了 mentioned；讀過的群（未讀 0）不標
    mentioned = (
        await _safe_notify(notifications_repo.unread_mention_group_ids(user_id))
        or set()
    )
    return {
        "success": True,
        "groups": [
            {**g, "mentioned": g["id"] in mentioned and g["unread_count"] > 0}
            for g in groups
        ],
    }


@router.post("/api/groups")
@limiter.limit("10/minute")
async def create_group(
    request: Request,
    body: CreateGroupRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    await _require_pro(user_id)
    result = await group_chat_repo.create_group(user_id, body.name)
    if not result["success"]:
        _raise(result["error"])
    group = result["group"]
    invited, skipped = [], []
    if body.invitee_ids:
        invites = await _send_invites(group["id"], user_id, body.invitee_ids)
        invited, skipped = invites.get("invited", []), invites.get("skipped", [])
    await _broadcast(
        [user_id], {"type": "group_updated", "group_id": group["id"], "kind": "created"}
    )
    return {"success": True, "group": group, "invited": invited, "skipped": skipped}


# /invites 要排在 /{group_id} 前面（不然 "invites" 會被當成 group_id 驗證失敗）
@router.get("/api/groups/invites")
@limiter.limit("60/minute")
async def list_invites(
    request: Request, current_user: dict = Depends(get_current_user)
):
    return {
        "success": True,
        "invites": await group_chat_repo.list_invites(current_user["user_id"]),
    }


@router.post("/api/groups/invites/{invite_id}/accept")
@limiter.limit("20/minute")
async def accept_invite(
    request: Request, invite_id: int, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    await _require_pro(user_id)
    result = await group_chat_repo.accept_invite(invite_id, user_id)
    if not result["success"]:
        _raise(result["error"])
    await _clear_notifications(
        user_id,
        await _safe_notify(
            notifications_repo.resolve_group_invite_notifications(user_id, invite_id)
        )
        or [],
    )
    await _broadcast_system(
        result["group_id"],
        result["member_ids"],
        [result["system_message"]],
        "member_joined",
    )
    return {"success": True, "group_id": result["group_id"]}


@router.post("/api/groups/invites/{invite_id}/decline")
@limiter.limit("20/minute")
async def decline_invite(
    request: Request, invite_id: int, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    result = await group_chat_repo.decline_invite(invite_id, user_id)
    if not result["success"]:
        _raise(result["error"])
    await _clear_notifications(
        user_id,
        await _safe_notify(
            notifications_repo.resolve_group_invite_notifications(user_id, invite_id)
        )
        or [],
    )
    return {"success": True}


@router.get("/api/groups/{group_id}")
@limiter.limit("60/minute")
async def get_group(
    request: Request, group_id: int, current_user: dict = Depends(get_current_user)
):
    group = await group_chat_repo.get_group(group_id, current_user["user_id"])
    if group is None:
        _raise("not_found")
    return {"success": True, "group": group}


@router.patch("/api/groups/{group_id}")
@limiter.limit("20/minute")
async def update_group(
    request: Request,
    group_id: int,
    body: UpdateGroupRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    await _require_pro(user_id)
    result = await group_chat_repo.update_group(
        group_id, user_id, name=body.name, history_visible=body.history_visible
    )
    if not result["success"]:
        _raise(result["error"])
    if result["system_messages"]:
        member_ids = await group_chat_repo.member_ids(group_id)
        await _broadcast_system(
            group_id, member_ids, result["system_messages"], "settings"
        )
    return {"success": True, "group": result["group"]}


@router.post("/api/groups/{group_id}/leave")
@limiter.limit("20/minute")
async def leave_group(
    request: Request, group_id: int, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    result = await group_chat_repo.leave_group(group_id, user_id)
    if not result["success"]:
        _raise(result["error"])
    if not result["deleted"]:
        await _broadcast_system(
            group_id, result["member_ids"], result["system_messages"], "member_left"
        )
    # 群刪了：還沒處理的邀請跟著消失，被邀的人鈴鐺裡的「接受」鈕也要拿掉
    for invite in result.get("cancelled_invites", []):
        ids = await _safe_notify(
            notifications_repo.resolve_group_invite_notifications(
                invite["invitee_id"], invite["invite_id"]
            )
        )
        await _clear_notifications(invite["invitee_id"], ids or [])
    # 自己其他裝置／分頁也要把這群拿掉
    await _broadcast(
        [user_id], {"type": "group_updated", "group_id": group_id, "kind": "left"}
    )
    return {"success": True, "deleted": result["deleted"]}


@router.delete("/api/groups/{group_id}/members/{user_id}")
@limiter.limit("20/minute")
async def remove_member(
    request: Request,
    group_id: int,
    user_id: str,
    current_user: dict = Depends(get_current_user),
):
    owner_id = current_user["user_id"]
    await _require_pro(owner_id)
    result = await group_chat_repo.remove_member(group_id, owner_id, user_id)
    if not result["success"]:
        _raise(result["error"])
    await _broadcast_system(
        group_id, result["member_ids"], [result["system_message"]], "member_removed"
    )
    # 被踢的人：畫面上拿掉這群＋通知（不然只會發現群不見了）
    await _broadcast(
        [user_id], {"type": "group_updated", "group_id": group_id, "kind": "removed"}
    )
    notification = await _safe_notify(
        notifications_repo.create_notification(
            user_id=user_id,
            notification_type="group_removed",
            title="已被移出群組",
            body=f"你已被移出「{result['group_name']}」",
            data={"group_id": group_id, "group_name": result["group_name"]},
        )
    )
    if notification:
        await push_notification_to_user(user_id, notification)
    return {"success": True}


@router.post("/api/groups/{group_id}/owner")
@limiter.limit("10/minute")
async def transfer_owner(
    request: Request,
    group_id: int,
    body: TransferOwnerRequest,
    current_user: dict = Depends(get_current_user),
):
    """群主手動交棒（自己還在群裡，變一般成員）"""
    owner_id = current_user["user_id"]
    await _require_pro(owner_id)
    result = await group_chat_repo.transfer_owner(group_id, owner_id, body.user_id)
    if not result["success"]:
        _raise(result["error"])
    await _broadcast_system(
        group_id, result["member_ids"], [result["system_message"]], "owner_changed"
    )
    return {"success": True, "owner_id": body.user_id}


@router.post("/api/groups/{group_id}/dissolve")
@limiter.limit("10/minute")
async def dissolve_group(
    request: Request, group_id: int, current_user: dict = Depends(get_current_user)
):
    """群主解散（c068）：成員全部移出、待處理邀請取消，訊息留著當檢舉證據。
    不要求 Pro（同退群：減法操作，Pro 到期的群主也要能收掉自己的群）。"""
    owner_id = current_user["user_id"]
    result = await group_chat_repo.dissolve_group(group_id, owner_id)
    if not result["success"]:
        _raise(result["error"])
    member_ids = result["member_ids"]
    # 所有原成員（含群主自己其他裝置）把這群拿掉；不推系統訊息（已經不是成員、讀不到了）
    await _broadcast(
        member_ids, {"type": "group_updated", "group_id": group_id, "kind": "dissolved"}
    )
    by_name = await _display_name(owner_id)
    for uid in member_ids:
        if uid == owner_id:
            continue
        notification = await _safe_notify(
            notifications_repo.create_notification(
                user_id=uid,
                notification_type="group_dissolved",
                title="群組已解散",
                body=f"{by_name} 解散了「{result['group_name']}」",
                data={
                    "group_id": group_id,
                    "group_name": result["group_name"],
                    "by_name": by_name,
                },
            )
        )
        if notification:
            await push_notification_to_user(uid, notification)
    # 邀請跟著取消：被邀的人鈴鐺裡的「接受」鈕要拿掉（同最後一人退群）
    for invite in result["cancelled_invites"]:
        ids = await _safe_notify(
            notifications_repo.resolve_group_invite_notifications(
                invite["invitee_id"], invite["invite_id"]
            )
        )
        await _clear_notifications(invite["invitee_id"], ids or [])
    return {"success": True}


@router.post("/api/groups/{group_id}/mute")
@limiter.limit("30/minute")
async def mute_group(
    request: Request,
    group_id: int,
    body: MuteRequest,
    current_user: dict = Depends(get_current_user),
):
    result = await group_chat_repo.set_muted(
        group_id, current_user["user_id"], body.muted
    )
    if not result["success"]:
        _raise(result["error"])
    return {"success": True, "muted": body.muted}


@router.post("/api/groups/{group_id}/invites")
@limiter.limit("20/minute")
async def invite_members(
    request: Request,
    group_id: int,
    body: InviteRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    await _require_pro(user_id)
    result = await _send_invites(group_id, user_id, body.user_ids)
    if not result["success"]:
        _raise(result["error"])
    return {"success": True, "invited": result["invited"], "skipped": result["skipped"]}


# ============================================================================
# 訊息
# ============================================================================


@router.get("/api/groups/{group_id}/messages")
@limiter.limit("60/minute")
async def get_group_messages(
    request: Request,
    group_id: int,
    before_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    result = await group_messages_repo.get_messages(
        group_id, current_user["user_id"], limit=limit, before_id=before_id
    )
    if not result["success"]:
        _raise(result["error"])
    return result


@router.post("/api/groups/{group_id}/messages")
@limiter.limit("30/minute")
async def send_group_message(
    request: Request,
    group_id: int,
    body: SendGroupMessageRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    await _require_pro(user_id)
    content, card = body.content, body.assistant_turn_id is not None
    if card:
        # AI 分析卡片：內容由伺服器取（自己問的、這個群、沒過期）
        turn = await chat_assistant_history_repo.get_turn_for_share(
            "group", group_id, user_id, body.assistant_turn_id
        )
        if turn is None:
            _raise("not_found")
        content = turn["answer"]
    result = await group_messages_repo.send_message(
        group_id,
        user_id,
        content,
        reply_to_message_id=body.reply_to_message_id,
        **({"message_type": CARD_TYPE} if card else {}),
    )
    if not result["success"]:
        _raise(result["error"])
    message = result["message"]
    from_name = (
        message.get("from_display_name") or message.get("from_username") or user_id
    )

    async def notify(uid: str, mentioned: bool) -> None:
        notification = await _safe_notify(
            notifications_repo.notify_group_message(
                to_user_id=uid,
                group_id=group_id,
                group_name=result.get("group_name") or "",
                from_user_id=user_id,
                from_name=from_name,
                message_preview=card_preview(message["content"])
                if card
                else message["content"],
                message_id=message["id"],
                mentioned=mentioned,
            )
        )
        if notification:
            await push_notification_to_user(uid, notification)

    # 被 @ 的人關了通知也發；排在推訊息之前：對方收到訊息重抓列表時「有人提及你」已經在
    mentioned_ids = result.get("mentioned_ids") or []
    for uid in mentioned_ids:
        await notify(uid, mentioned=True)

    # 全部成員都推（含自己其他裝置；前端用 id 去重）
    await _broadcast(
        result["member_ids"],
        {"type": "group_message", "group_id": group_id, "message": message},
    )

    for uid in await group_chat_repo.unmuted_member_ids(group_id):
        if uid == user_id or uid in mentioned_ids:
            continue
        await notify(uid, mentioned=False)
    return {"success": True, "message": message}


@router.post("/api/groups/{group_id}/read")
@limiter.limit("60/minute")
async def mark_group_read(
    request: Request,
    group_id: int,
    body: MarkGroupReadRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    result = await group_messages_repo.mark_read(
        group_id, user_id, body.last_message_id
    )
    if not result["success"]:
        _raise(result["error"])
    if result["changed"]:
        await _broadcast(
            result["member_ids"],
            {
                "type": "group_read",
                "group_id": group_id,
                "user_id": user_id,
                "last_read_message_id": result["last_read_message_id"],
            },
        )
    await _clear_notifications(
        user_id,
        await _safe_notify(
            notifications_repo.mark_group_message_notifications_read(user_id, group_id)
        )
        or [],
    )
    return {"success": True, "last_read_message_id": result["last_read_message_id"]}


@router.delete("/api/groups/messages/{message_id}")
@limiter.limit("20/minute")
async def recall_group_message(
    request: Request, message_id: int, current_user: dict = Depends(get_current_user)
):
    result = await group_messages_repo.recall_message(
        message_id, current_user["user_id"]
    )
    if not result["success"]:
        _raise(result["error"])
    group_id = result["group_id"]
    await _broadcast(
        result["member_ids"],
        {
            "type": "group_message_recalled",
            "group_id": group_id,
            "message_id": message_id,
        },
    )
    for notification in (
        await _safe_notify(
            notifications_repo.mark_group_message_recalled(group_id, message_id)
        )
        or []
    ):
        await push_notification_updated(notification["user_id"], notification)
    return {"success": True, "recalled": True}


@router.put("/api/groups/messages/{message_id}/reaction")
@limiter.limit("60/minute")
async def set_group_reaction(
    request: Request,
    message_id: int,
    body: GroupReactionRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    await _require_pro(user_id)
    result = await group_messages_repo.set_reaction(message_id, user_id, body.reaction)
    if not result["success"]:
        _raise(result["error"])
    await _push_reactions(result)
    return {"success": True, "reactions": result["reactions"]}


@router.delete("/api/groups/messages/{message_id}/reaction")
@limiter.limit("60/minute")
async def remove_group_reaction(
    request: Request, message_id: int, current_user: dict = Depends(get_current_user)
):
    result = await group_messages_repo.remove_reaction(
        message_id, current_user["user_id"]
    )
    if not result["success"]:
        _raise(result["error"])
    if result.get("changed"):
        await _push_reactions(result)
    return {"success": True, "reactions": result["reactions"]}


async def _push_reactions(result: dict) -> None:
    await _broadcast(
        result["member_ids"],
        {
            "type": "group_reaction_updated",
            "group_id": result["group_id"],
            "message_id": result["message_id"],
            "reactions": result["reactions"],
        },
    )


@router.post("/api/groups/messages/{message_id}/report")
@limiter.limit("10/hour")
async def report_group_message(
    request: Request,
    message_id: int,
    body: GroupReportRequest,
    current_user: dict = Depends(get_current_user),
):
    result = await group_messages_repo.report_message(
        message_id,
        current_user["user_id"],
        body.reason,
        (body.note or "").strip() or None,
    )
    if not result["success"]:
        _raise(result["error"])
    # 背景打風險分數（後台照危險程度排）：只看被檢舉的人在快照裡傳的
    if result.get("reported_user_id"):
        spawn(
            score_group_report(
                result["report_id"],
                dm_report_text(
                    result.get("snapshot") or [], result["reported_user_id"]
                ),
            )
        )
    return {"success": True}


# ============================================================================
# 「輸入中」（/ws/messages 收到帶 group_id 的 typing 轉到這裡）
# ============================================================================

_TYPING_MIN_INTERVAL = 1.0
_TYPING_ANY_INTERVAL = 0.2
_TYPING_AUTH_TTL = 60.0
_TYPING_MAX_GROUPS = 100


async def relay_group_typing(
    user_id: str,
    username: str,
    message: dict,
    state: dict,
    now: Callable[[], float] = time.monotonic,
) -> None:
    """把「輸入中」轉給同群其他成員。成員資格快取 60 秒（被踢最慢 60 秒停止收到）、節流同私訊。
    state 是這條 WS 連線自己的 dict。"""
    group_id = message.get("group_id")
    if type(group_id) is not int:  # bool 是 int 的子類，要擋
        return
    t = now()
    groups = state.setdefault("groups", {})
    last_start = state.setdefault("group_last_start", {})
    typing_state = "stop" if message.get("state") == "stop" else "start"

    if typing_state == "stop":
        cached = groups.get(group_id)
        if group_id not in last_start or not cached or not cached[0] or t >= cached[1]:
            return
        del last_start[group_id]
        await _send_group_typing(cached[0], group_id, user_id, username, "stop")
        return

    last_any = state.get("group_last_any")
    if last_any is not None and t - last_any < _TYPING_ANY_INTERVAL:
        return
    prev = last_start.get(group_id)
    if prev is not None and t - prev < _TYPING_MIN_INTERVAL:
        return
    state["group_last_any"] = t

    cached = groups.get(group_id)
    if cached and t < cached[1]:
        targets = cached[0]
    else:
        # 開關關著不轉（只在快取過期時查，不是每個按鍵都查設定）
        if not await config_repo.get_config("group_chat_enabled", False):
            members = []
        else:
            members = await group_chat_repo.member_ids(group_id)
        targets = [m for m in members if m != user_id] if user_id in members else None
        groups.pop(group_id, None)
        if len(groups) >= _TYPING_MAX_GROUPS:
            del groups[next(iter(groups))]
        groups[group_id] = (targets, t + _TYPING_AUTH_TTL)
    if not targets:
        return
    last_start[group_id] = t
    await _send_group_typing(targets, group_id, user_id, username, "start")


async def _send_group_typing(
    targets: List[str], group_id: int, user_id: str, username: str, state: str
) -> None:
    await _broadcast(
        targets,
        {
            "type": "group_typing",
            "group_id": group_id,
            "from_user_id": user_id,
            "from_username": username,
            "state": state,
        },
    )
