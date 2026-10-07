"""
Async ORM repository for DM conversation and message operations.

Provides async equivalents of the functions in core.database.messages,
using SQLAlchemy 2.0 select/update/insert with ORM models.

Usage::

    from core.orm.messages_repo import messages_repo

    conv = await messages_repo.get_or_create_conversation("user1", "user2")
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import and_, case, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_card import CARD_MAX_LENGTH, CARD_TYPE, card_preview
from core.dm_reactions import REACTION_KEYS
from core.dm_reply import build_reply_preview

from .chat_assistant_history_repo import chat_assistant_history_repo
from .models import (
    ChatOrder,
    ChatPin,
    DmConversation,
    DmMessage,
    DmMessageDeletion,
    DmMessageReaction,
    Friendship,
    User,
)
from .session import using_session

logger = logging.getLogger(__name__)

_DEFAULT_MAX_LENGTH = 500

# 收回時限（LINE 是 24 小時）；前端只決定要不要顯示「收回」，以這裡為準
RECALL_WINDOW = timedelta(hours=24)


def _dt_iso(val: Optional[datetime]) -> Optional[str]:
    return val.isoformat() if val else None


def visible_conversation_clause(user_id: str):
    """私訊列表看得到的對話（get_conversations 與 chat_pins_repo 的失效置頂共用，對齊 DmConversation）：
    - 是參與者
    - 至少有一則沒被自己刪掉的訊息（「刪除對話」＝整段訊息只對自己隱藏；之後有新訊息就再出現）
    - 不是我封鎖的人：我先封鎖的，或互相封鎖（對方先封鎖、mutual_block）時我是後封鎖的那方
    """

    # 兩個角色各一個子查詢：在 EXISTS 裡用 CASE 會被 SQLAlchemy 自動 correlate 搞錯
    def _i_blocked(other):
        return exists(
            select(Friendship.id).where(
                Friendship.status == "blocked",
                or_(
                    (Friendship.user_id == user_id) & (Friendship.friend_id == other),
                    (Friendship.user_id == other)
                    & (Friendship.friend_id == user_id)
                    & Friendship.mutual_block.is_(True),
                ),
            )
        ).correlate(DmConversation)

    has_visible_msg = exists(
        select(DmMessage.id)
        .outerjoin(
            DmMessageDeletion,
            and_(
                DmMessageDeletion.message_id == DmMessage.id,
                DmMessageDeletion.user_id == user_id,
            ),
        )
        .where(
            DmMessage.conversation_id == DmConversation.id,
            DmMessageDeletion.id.is_(None),
        )
    ).correlate(DmConversation)

    return and_(
        or_(DmConversation.user1_id == user_id, DmConversation.user2_id == user_id),
        has_visible_msg,
        ~_i_blocked(DmConversation.user1_id),
        ~_i_blocked(DmConversation.user2_id),
    )


def _conv_row_to_dict(row) -> dict:
    cols = row._mapping
    return {
        "id": cols[DmConversation.id],
        "user1_id": cols[DmConversation.user1_id],
        "user2_id": cols[DmConversation.user2_id],
        "last_message_at": _dt_iso(cols[DmConversation.last_message_at]),
        "user1_unread_count": cols[DmConversation.user1_unread_count],
        "user2_unread_count": cols[DmConversation.user2_unread_count],
        "created_at": _dt_iso(cols[DmConversation.created_at]),
    }


def _msg_row_to_dict(row) -> dict:
    cols = row._mapping
    return {
        "id": cols[DmMessage.id],
        "conversation_id": cols[DmMessage.conversation_id],
        "from_user_id": cols[DmMessage.from_user_id],
        "to_user_id": cols[DmMessage.to_user_id],
        "content": cols[DmMessage.content],
        "message_type": cols[DmMessage.message_type],
        "is_read": bool(cols[DmMessage.is_read]),
        "read_at": _dt_iso(cols[DmMessage.read_at]),
        "created_at": _dt_iso(cols[DmMessage.created_at]),
        "from_username": cols.get("_from_username"),
        "to_username": cols.get("_to_username"),
        # 暱稱（沒設是 None）：前端顯示 display_name || username
        "from_display_name": cols.get("_from_display_name"),
        "to_display_name": cols.get("_to_display_name"),
        "reply_to_message_id": cols[DmMessage.reply_to_message_id],
    }


async def _attach_reply_previews(s: AsyncSession, messages: List[dict]) -> List[dict]:
    """每則補上 reply_to 預覽（被回覆那則的發送者、暱稱、前 100 字、是否已收回）。
    一次查完，不在主查詢裡 self-join（兩條讀取路徑都要，主查詢保持原樣）。"""
    parent_ids = {
        m["reply_to_message_id"] for m in messages if m["reply_to_message_id"]
    }
    previews = {}
    if parent_ids:
        pu = User.__table__.alias("pu")
        rows = (
            await s.execute(
                select(
                    DmMessage.id,
                    DmMessage.from_user_id,
                    pu.c.display_name,
                    pu.c.username,
                    DmMessage.content,
                    DmMessage.message_type,
                )
                .outerjoin(pu, pu.c.user_id == DmMessage.from_user_id)
                .where(DmMessage.id.in_(parent_ids))
            )
        ).all()
        previews = {row[0]: build_reply_preview(*row) for row in rows}
    return [{**m, "reply_to": previews.get(m["reply_to_message_id"])} for m in messages]


async def _reactions_by_message(s: AsyncSession, message_ids) -> dict:
    """{message_id: [{user_id, reaction}, ...]}，依按下的先後排"""
    ids = list(message_ids)
    if not ids:
        return {}
    rows = (
        await s.execute(
            select(
                DmMessageReaction.message_id,
                DmMessageReaction.user_id,
                DmMessageReaction.reaction,
            )
            .where(DmMessageReaction.message_id.in_(ids))
            .order_by(DmMessageReaction.created_at, DmMessageReaction.id)
        )
    ).all()
    grouped: dict = {}
    for message_id, user_id, reaction in rows:
        grouped.setdefault(message_id, []).append(
            {"user_id": user_id, "reaction": reaction}
        )
    return grouped


async def _attach_reactions(s: AsyncSession, messages: List[dict]) -> List[dict]:
    grouped = await _reactions_by_message(s, (m["id"] for m in messages))
    return [{**m, "reactions": grouped.get(m["id"], [])} for m in messages]


class MessagesRepository:
    async def get_or_create_conversation(
        self,
        user1_id: str,
        user2_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        if user1_id > user2_id:
            user1_id, user2_id = user2_id, user1_id

        async with using_session(session) as s:
            result = await s.execute(
                select(DmConversation).where(
                    DmConversation.user1_id == user1_id,
                    DmConversation.user2_id == user2_id,
                )
            )
            conv = result.scalar_one_or_none()
            if conv:
                return {
                    "id": conv.id,
                    "user1_id": conv.user1_id,
                    "user2_id": conv.user2_id,
                    "last_message_at": _dt_iso(conv.last_message_at),
                    "user1_unread_count": conv.user1_unread_count,
                    "user2_unread_count": conv.user2_unread_count,
                    "created_at": _dt_iso(conv.created_at),
                    "is_new": False,
                }

            now = datetime.now(timezone.utc)
            conv = DmConversation(
                user1_id=user1_id,
                user2_id=user2_id,
                created_at=now,
            )
            s.add(conv)
            await s.flush()
            return {
                "id": conv.id,
                "user1_id": conv.user1_id,
                "user2_id": conv.user2_id,
                "last_message_at": None,
                "user1_unread_count": 0,
                "user2_unread_count": 0,
                "created_at": now.isoformat(),
                "is_new": True,
            }

    async def get_conversations(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        order: str = "recent",
        session: AsyncSession | None = None,
    ) -> List[dict]:
        """私訊列表。置頂的（c067）一律照使用者排的順序在最前面、一定在第一頁。
        order="custom"（c068 自訂順序）：其他的先放還沒排過位置的（新對話，照最後動態），
        再照 chat_order 的位置；新訊息不會把對話往上推。order="recent"：其他照最後動態。"""
        u1 = User.__table__.alias("u1")
        u2 = User.__table__.alias("u2")

        is_user1 = DmConversation.user1_id == user_id
        other_user_id_expr = case(
            (is_user1, DmConversation.user2_id),
            else_=DmConversation.user1_id,
        )
        other_username_expr = case(
            (is_user1, u2.c.username),
            else_=u1.c.username,
        )
        other_display_expr = case(
            (is_user1, u2.c.display_name),
            else_=u1.c.display_name,
        )
        other_tier_expr = case(
            (is_user1, u2.c.membership_tier),
            else_=u1.c.membership_tier,
        )

        stmt = (
            select(
                DmConversation.id,
                DmConversation.user1_id,
                DmConversation.user2_id,
                DmConversation.last_message_at,
                DmConversation.user1_unread_count,
                DmConversation.user2_unread_count,
                DmConversation.created_at,
                DmMessage.content.label("last_message"),
                DmMessage.from_user_id.label("last_message_from"),
                DmMessage.message_type.label("last_message_type"),
                other_username_expr.label("other_username"),
                other_display_expr.label("other_display_name"),
                other_user_id_expr.label("other_user_id"),
                other_tier_expr.label("other_membership_tier"),
                ChatPin.position.label("pin_position"),
                ChatOrder.position.label("order_position"),
            )
            .outerjoin(
                DmMessage,
                DmMessage.id == DmConversation.last_message_id,
            )
            .outerjoin(u1, u1.c.user_id == DmConversation.user1_id)
            .outerjoin(u2, u2.c.user_id == DmConversation.user2_id)
            .outerjoin(
                ChatPin,
                and_(
                    ChatPin.user_id == user_id,
                    ChatPin.kind == "dm",
                    ChatPin.target_id == DmConversation.id,
                ),
            )
            .outerjoin(
                ChatOrder,
                and_(
                    ChatOrder.user_id == user_id,
                    ChatOrder.kind == "dm",
                    ChatOrder.target_id == DmConversation.id,
                ),
            )
            .where(visible_conversation_clause(user_id))
        )
        recency = (
            DmConversation.last_message_at.desc().nullslast(),
            DmConversation.created_at.desc(),
        )
        if order == "custom":
            # 最後補 id：排序要是全序，limit/offset 分頁才不會重複或漏掉
            stmt = stmt.order_by(
                ChatPin.position.asc().nullslast(),
                ChatOrder.position.asc().nullsfirst(),
                *recency,
                DmConversation.id.desc(),
            )
        else:
            stmt = stmt.order_by(ChatPin.position.asc().nullslast(), *recency)
        stmt = stmt.limit(limit).offset(offset)

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.all()
            conversations = []
            for row in rows:
                cols = row._mapping
                user1_id_val = cols[DmConversation.user1_id]
                unread_count = (
                    cols[DmConversation.user1_unread_count]
                    if user_id == user1_id_val
                    else cols[DmConversation.user2_unread_count]
                )
                conversations.append(
                    {
                        "id": cols[DmConversation.id],
                        "other_user_id": cols["other_user_id"],
                        "other_username": cols["other_username"]
                        or cols["other_user_id"],
                        "other_display_name": cols["other_display_name"],
                        "other_membership_tier": cols["other_membership_tier"]
                        or "free",
                        # 卡片整篇不送進列表，只給一行預覽
                        "last_message": card_preview(cols["last_message"], 100)
                        if cols["last_message_type"] == CARD_TYPE
                        else cols["last_message"],
                        "last_message_from": cols["last_message_from"],
                        "last_message_type": cols["last_message_type"],
                        "last_message_at": _dt_iso(
                            cols[DmConversation.last_message_at]
                        ),
                        "unread_count": unread_count,
                        "created_at": _dt_iso(cols[DmConversation.created_at]),
                        "pin_position": cols["pin_position"],
                        "order_position": cols["order_position"],
                    }
                )
            return conversations

    async def get_conversation_by_id(
        self,
        conversation_id: int,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> Optional[dict]:
        stmt = select(DmConversation).where(
            DmConversation.id == conversation_id,
            or_(
                DmConversation.user1_id == user_id,
                DmConversation.user2_id == user_id,
            ),
        )
        async with using_session(session) as s:
            result = await s.execute(stmt)
            conv = result.scalar_one_or_none()
            if not conv:
                return None
            return {
                "id": conv.id,
                "user1_id": conv.user1_id,
                "user2_id": conv.user2_id,
                "last_message_at": _dt_iso(conv.last_message_at),
                "user1_unread_count": conv.user1_unread_count,
                "user2_unread_count": conv.user2_unread_count,
            }

    async def validate_message_send(
        self,
        from_user_id: str,
        to_user_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        sender_exists_sq = (
            exists(select(User.user_id).where(User.user_id == from_user_id))
            .correlate(None)
            .label("sender_exists")
        )

        receiver_exists_sq = (
            exists(select(User.user_id).where(User.user_id == to_user_id))
            .correlate(None)
            .label("receiver_exists")
        )

        are_friends_sq = (
            exists(
                select(Friendship.id).where(
                    or_(
                        and_(
                            Friendship.user_id == from_user_id,
                            Friendship.friend_id == to_user_id,
                        ),
                        and_(
                            Friendship.user_id == to_user_id,
                            Friendship.friend_id == from_user_id,
                        ),
                    ),
                    Friendship.status == "accepted",
                )
            )
            .correlate(None)
            .label("are_friends")
        )

        is_blocked_sq = (
            exists(
                select(Friendship.id).where(
                    or_(
                        and_(
                            Friendship.user_id == from_user_id,
                            Friendship.friend_id == to_user_id,
                        ),
                        and_(
                            Friendship.user_id == to_user_id,
                            Friendship.friend_id == from_user_id,
                        ),
                    ),
                    Friendship.status == "blocked",
                )
            )
            .correlate(None)
            .label("is_blocked")
        )

        stmt = select(
            sender_exists_sq,
            receiver_exists_sq,
            are_friends_sq,
            is_blocked_sq,
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            row = result.one()
            sender_exists, receiver_exists, are_friends, is_blocked = row

            if not sender_exists:
                return {"valid": False, "error": "sender_not_found"}
            if not receiver_exists:
                return {"valid": False, "error": "receiver_not_found"}
            if is_blocked:
                return {"valid": False, "error": "blocked"}
            if not are_friends:
                return {"valid": False, "error": "not_friends"}

            return {
                "valid": True,
                "sender_exists": sender_exists,
                "receiver_exists": receiver_exists,
                "are_friends": are_friends,
                "is_blocked": is_blocked,
            }

    async def check_reply_target(
        self,
        from_user_id: str,
        to_user_id: str,
        reply_to_message_id: int,
        session: AsyncSession | None = None,
    ) -> Optional[str]:
        """被回覆那則要在這兩人的對話裡（別的對話一律當不存在，不洩漏 id）、而且還沒被收回。
        合法回 None，不然回錯誤碼。"""
        user1_id, user2_id = sorted((from_user_id, to_user_id))
        async with using_session(session) as s:
            target = (
                await s.execute(
                    select(DmMessage.message_type)
                    .join(
                        DmConversation, DmConversation.id == DmMessage.conversation_id
                    )
                    .where(
                        DmMessage.id == reply_to_message_id,
                        DmConversation.user1_id == user1_id,
                        DmConversation.user2_id == user2_id,
                    )
                )
            ).one_or_none()
        if target is None:
            return "reply_target_not_found"
        if target[0] == "recalled":
            return "reply_target_recalled"
        return None

    async def send_message(
        self,
        from_user_id: str,
        to_user_id: str,
        content: str,
        message_type: str = "text",
        reply_to_message_id: Optional[int] = None,
        session: AsyncSession | None = None,
    ) -> dict:
        if from_user_id == to_user_id:
            return {"success": False, "error": "cannot_message_self"}
        if not content or not content.strip():
            return {"success": False, "error": "empty_content"}

        # AI 分析卡片不受一般訊息的字數上限管（內容由伺服器取自 AI 回答，見 core/ai_card.py）
        if message_type == CARD_TYPE:
            max_length = CARD_MAX_LENGTH
        else:
            max_length = await self._get_message_config(
                "limit_message_max_length", _DEFAULT_MAX_LENGTH, session
            )
        if len(content) > max_length:
            return {
                "success": False,
                "error": "message_too_long",
                "max_length": max_length,
            }

        async with using_session(session) as s:
            try:
                await s.execute(
                    update(User)
                    .where(User.user_id == from_user_id)
                    .values(last_active_at=datetime.now(timezone.utc))
                )

                user1_id, user2_id = (
                    (from_user_id, to_user_id)
                    if from_user_id < to_user_id
                    else (to_user_id, from_user_id)
                )

                # 回覆：router 扣額度前已驗過；這裡再驗一次，擋「驗完到寫入之間被收回」。
                # 先驗再建對話，免得驗失敗留下一個空對話
                if reply_to_message_id is not None:
                    error = await self.check_reply_target(
                        from_user_id, to_user_id, reply_to_message_id, session=s
                    )
                    if error:
                        return {"success": False, "error": error}

                result = await s.execute(
                    select(DmConversation.id, DmConversation.user1_id).where(
                        DmConversation.user1_id == user1_id,
                        DmConversation.user2_id == user2_id,
                    )
                )
                conv_row = result.one_or_none()

                if conv_row:
                    conversation_id, conv_user1_id = conv_row
                else:
                    now = datetime.now(timezone.utc)
                    conv = DmConversation(
                        user1_id=user1_id,
                        user2_id=user2_id,
                        created_at=now,
                    )
                    s.add(conv)
                    await s.flush()
                    conversation_id = conv.id
                    conv_user1_id = user1_id

                msg = DmMessage(
                    conversation_id=conversation_id,
                    from_user_id=from_user_id,
                    to_user_id=to_user_id,
                    content=content.strip(),
                    message_type=message_type,
                    reply_to_message_id=reply_to_message_id,
                )
                s.add(msg)
                await s.flush()

                if conv_user1_id == to_user_id:
                    await s.execute(
                        update(DmConversation)
                        .where(DmConversation.id == conversation_id)
                        .values(
                            last_message_id=msg.id,
                            last_message_at=datetime.now(timezone.utc),
                            user1_unread_count=DmConversation.user1_unread_count + 1,
                        )
                    )
                else:
                    await s.execute(
                        update(DmConversation)
                        .where(DmConversation.id == conversation_id)
                        .values(
                            last_message_id=msg.id,
                            last_message_at=datetime.now(timezone.utc),
                            user2_unread_count=DmConversation.user2_unread_count + 1,
                        )
                    )

                await s.flush()

                from_u = User.__table__.alias("from_u")
                to_u = User.__table__.alias("to_u")
                stmt = (
                    select(
                        DmMessage.id,
                        DmMessage.conversation_id,
                        DmMessage.from_user_id,
                        DmMessage.to_user_id,
                        DmMessage.content,
                        DmMessage.message_type,
                        DmMessage.reply_to_message_id,
                        DmMessage.is_read,
                        DmMessage.read_at,
                        DmMessage.created_at,
                        from_u.c.username.label("_from_username"),
                        to_u.c.username.label("_to_username"),
                        from_u.c.display_name.label("_from_display_name"),
                        to_u.c.display_name.label("_to_display_name"),
                    )
                    .outerjoin(from_u, from_u.c.user_id == DmMessage.from_user_id)
                    .outerjoin(to_u, to_u.c.user_id == DmMessage.to_user_id)
                    .where(DmMessage.id == msg.id)
                )

                result = await s.execute(stmt)
                msg_row = result.one()
                (message,) = await _attach_reply_previews(
                    s, [_msg_row_to_dict(msg_row)]
                )
                message["reactions"] = []  # 剛送出，還沒人按

                return {
                    "success": True,
                    "message": message,
                }
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error("send_message error: %s", e, exc_info=True)
                return {"success": False, "error": str(e)}

    async def get_messages(
        self,
        conversation_id: int,
        user_id: str,
        limit: int = 50,
        before_id: Optional[int] = None,
        session: AsyncSession | None = None,
    ) -> dict:
        from_u = User.__table__.alias("from_u")
        to_u = User.__table__.alias("to_u")

        verify_stmt = select(DmConversation.id).where(
            DmConversation.id == conversation_id,
            or_(
                DmConversation.user1_id == user_id,
                DmConversation.user2_id == user_id,
            ),
        )

        msg_stmt = (
            select(
                DmMessage.id,
                DmMessage.conversation_id,
                DmMessage.from_user_id,
                DmMessage.to_user_id,
                DmMessage.content,
                DmMessage.message_type,
                DmMessage.reply_to_message_id,
                DmMessage.is_read,
                DmMessage.read_at,
                DmMessage.created_at,
                from_u.c.username.label("_from_username"),
                to_u.c.username.label("_to_username"),
                from_u.c.display_name.label("_from_display_name"),
                to_u.c.display_name.label("_to_display_name"),
            )
            .outerjoin(from_u, from_u.c.user_id == DmMessage.from_user_id)
            .outerjoin(to_u, to_u.c.user_id == DmMessage.to_user_id)
            # 排除自己「刪除（只對自己隱藏）」的訊息——同第一頁的
            # get_conversation_with_messages；沒排除時捲上去翻頁就冒出來
            .outerjoin(
                DmMessageDeletion,
                and_(
                    DmMessageDeletion.message_id == DmMessage.id,
                    DmMessageDeletion.user_id == user_id,
                ),
            )
            .where(
                DmMessage.conversation_id == conversation_id,
                DmMessageDeletion.id.is_(None),
            )
        )

        if before_id is not None:
            msg_stmt = msg_stmt.where(DmMessage.id < before_id)

        msg_stmt = msg_stmt.order_by(
            DmMessage.created_at.desc(), DmMessage.id.desc()
        ).limit(limit)

        async with using_session(session) as s:
            verify_result = await s.execute(verify_stmt)
            if not verify_result.scalar_one_or_none():
                return {"success": False, "error": "conversation_not_found"}

            result = await s.execute(msg_stmt)
            rows = result.all()
            messages = await _attach_reactions(
                s,
                await _attach_reply_previews(
                    s, [_msg_row_to_dict(row) for row in rows]
                ),
            )
            messages.reverse()

            return {
                "success": True,
                "messages": messages,
                "has_more": len(rows) == limit,
            }

    async def mark_as_read(
        self,
        conversation_id: int,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        conv = await self.get_conversation_by_id(conversation_id, user_id, session)
        if not conv:
            return {"success": False, "error": "conversation_not_found"}

        async with using_session(session) as s:
            result = await s.execute(
                update(DmMessage)
                .where(
                    DmMessage.conversation_id == conversation_id,
                    DmMessage.to_user_id == user_id,
                    DmMessage.from_user_id != user_id,
                    DmMessage.is_read == 0,
                )
                .values(is_read=1, read_at=datetime.now(timezone.utc))
            )
            updated_count = result.rowcount

            if conv["user1_id"] == user_id:
                await s.execute(
                    update(DmConversation)
                    .where(DmConversation.id == conversation_id)
                    .values(user1_unread_count=0)
                )
            else:
                await s.execute(
                    update(DmConversation)
                    .where(DmConversation.id == conversation_id)
                    .values(user2_unread_count=0)
                )

            return {
                "success": True,
                "marked_count": updated_count,
            }

    async def recall_message(
        self,
        message_id: int,
        user_id: str,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> dict:
        """收回（僅發送者、24 小時內）：清空原文——不能只改類型，原文留在 DB 就會從
        列表預覽、搜尋、API 回應、通知漏出去（2026-09-29 查到四處）。同一交易改通知。"""
        from .notifications_repo import notifications_repo

        async with using_session(session) as s:
            msg = (
                await s.execute(
                    select(DmMessage)
                    .where(DmMessage.id == message_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            # 不是參與者就當不存在，不洩漏 id 是否存在
            if msg is None or user_id not in (msg.from_user_id, msg.to_user_id):
                return {"success": False, "error": "message_not_found"}
            if msg.from_user_id != user_id:
                return {"success": False, "error": "permission_denied"}
            if msg.message_type == "recalled":
                return {"success": False, "error": "already_recalled"}
            now = now or datetime.now(timezone.utc)
            if msg.created_at and now - msg.created_at > RECALL_WINDOW:
                return {"success": False, "error": "recall_window_expired"}

            original = msg.content
            msg.content = ""
            msg.message_type = "recalled"
            # 收回的訊息不留表情（同一交易；按表情那邊也鎖同一列，不會插回來）
            await s.execute(
                delete(DmMessageReaction).where(DmMessageReaction.message_id == msg.id)
            )
            await s.flush()
            # AI 助理整理過這則的問答也要作廢（同一交易）
            await chat_assistant_history_repo.invalidate_message(
                "dm", msg.id, session=s
            )
            notifications = await notifications_repo.mark_message_recalled(
                msg.to_user_id, msg.conversation_id, msg.id, original, session=s
            )
            return {
                "success": True,
                "recalled": True,
                "message_id": msg.id,
                "conversation_id": msg.conversation_id,
                "from_user_id": msg.from_user_id,
                "to_user_id": msg.to_user_id,
                "notifications": notifications,
            }

    async def _locked_reactable_message(
        self, s: AsyncSession, message_id: int, user_id: str
    ):
        """按表情前的檢查：參與者、未收回、雙方沒有封鎖。回 (DmMessage, None) 或 (None, error)。
        鎖住訊息列：跟收回（也鎖這列）排隊，收回後不會再被插回表情。"""
        msg = (
            await s.execute(
                select(DmMessage).where(DmMessage.id == message_id).with_for_update()
            )
        ).scalar_one_or_none()
        if msg is None or user_id not in (msg.from_user_id, msg.to_user_id):
            return None, "message_not_found"
        if msg.message_type == "recalled":
            return None, "message_recalled"
        other = msg.to_user_id if user_id == msg.from_user_id else msg.from_user_id
        blocked = (
            await s.execute(
                select(Friendship.id)
                .where(
                    or_(
                        and_(
                            Friendship.user_id == user_id, Friendship.friend_id == other
                        ),
                        and_(
                            Friendship.user_id == other, Friendship.friend_id == user_id
                        ),
                    ),
                    Friendship.status == "blocked",
                )
                .limit(1)
            )
        ).first()
        if blocked:
            return None, "blocked"
        return msg, None

    async def _reaction_result(self, s: AsyncSession, msg) -> dict:
        grouped = await _reactions_by_message(s, [msg.id])
        return {
            "success": True,
            "conversation_id": msg.conversation_id,
            "participants": [msg.from_user_id, msg.to_user_id],
            "reactions": grouped.get(msg.id, []),
        }

    async def set_reaction(
        self,
        message_id: int,
        user_id: str,
        reaction: str,
        session: AsyncSession | None = None,
    ) -> dict:
        """按表情：每人每則一個，按別的就替換。回傳這則目前全部的表情（給 WS 推）。"""
        if reaction not in REACTION_KEYS:
            return {"success": False, "error": "invalid_reaction"}
        async with using_session(session) as s:
            msg, error = await self._locked_reactable_message(s, message_id, user_id)
            if error:
                return {"success": False, "error": error}
            await s.execute(
                pg_insert(DmMessageReaction)
                .values(message_id=msg.id, user_id=user_id, reaction=reaction)
                .on_conflict_do_update(
                    constraint="uq_dm_message_reaction",
                    set_={"reaction": reaction, "created_at": func.now()},
                )
            )
            return await self._reaction_result(s, msg)

    async def remove_reaction(
        self,
        message_id: int,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        """收回自己的表情（沒按過也回成功）"""
        async with using_session(session) as s:
            msg, error = await self._locked_reactable_message(s, message_id, user_id)
            if error:
                return {"success": False, "error": error}
            deleted = await s.execute(
                delete(DmMessageReaction).where(
                    DmMessageReaction.message_id == msg.id,
                    DmMessageReaction.user_id == user_id,
                )
            )
            # changed：本來就沒按的話不用推 WS（router 看這個）
            return {
                **await self._reaction_result(s, msg),
                "changed": deleted.rowcount > 0,
            }

    async def get_unread_count(
        self,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> int:
        """私訊未讀總數。只算列表看得到的對話（同 get_conversations 的 visible_conversation_clause）：
        刪除對話、封鎖之後列表看不到，數字還算的話使用者看不到也清不掉。"""
        unread_expr = case(
            (DmConversation.user1_id == user_id, DmConversation.user1_unread_count),
            else_=DmConversation.user2_unread_count,
        )
        # unread > 0 放前面：沒未讀的對話不必再跑可見性的子查詢
        stmt = select(func.coalesce(func.sum(unread_expr), 0)).where(
            unread_expr > 0,
            visible_conversation_clause(user_id),
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            return result.scalar() or 0

    async def _get_message_config(
        self,
        key: str,
        default,
        session: AsyncSession | None = None,
    ):
        from .models import SystemConfig

        stmt = select(SystemConfig.value, SystemConfig.value_type).where(
            SystemConfig.key == key
        )
        async with using_session(session) as s:
            result = await s.execute(stmt)
            row = result.one_or_none()
            if not row:
                return default
            value, value_type = row
            if value == "null" or value is None:
                return None
            if value_type == "int":
                return int(value)
            if value_type == "float":
                return float(value)
            if value_type == "bool":
                return value.lower() in ("true", "1", "yes")
            return value


messages_repo = MessagesRepository()
