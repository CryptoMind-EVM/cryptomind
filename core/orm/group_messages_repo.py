"""
群組聊天：訊息、已讀、表情、收回、檢舉（c066，設計 docs/plans/2026-10-01-group-chat-design.md）。

可見範圍：成員只看得到 id > first_visible_message_id 的訊息。讀取、往上翻、回覆目標、
表情、檢舉快照都以這條線為界（看不到的一律當不存在，不洩漏 id）。
收回比照私訊：清空原文、清掉表情，24 小時內。系統訊息不能被收回、按表情、檢舉。
群組、成員、邀請在 group_chat_repo。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_card import CARD_MAX_LENGTH, CARD_TYPE
from core.dm_reactions import REACTION_KEYS
from core.dm_reply import build_reply_preview
from core.group_mentions import find_mentions

from .chat_assistant_history_repo import chat_assistant_history_repo
from .config_repo import config_repo
from .group_chat_repo import (
    _member_ids,
    display_names,
    is_member,
    message_dict,
    system_user_id,
)
from .models import (
    GroupChat,
    GroupMember,
    GroupMessage,
    GroupMessageReaction,
    GroupReport,
    User,
)
from .session import using_session

DEFAULT_MAX_LENGTH = 500
RECALL_WINDOW = timedelta(hours=24)  # 同私訊（LINE 是 24 小時）
REPORT_REASONS = ("scam", "harassment", "spam", "other")
SNAPSHOT_BEFORE = 10


async def _names(s: AsyncSession, user_ids) -> dict:
    ids = {u for u in user_ids if u}
    if not ids:
        return {}
    rows = await s.execute(
        select(User.user_id, User.username, User.display_name).where(
            User.user_id.in_(ids)
        )
    )
    return {uid: (username, display_name) for uid, username, display_name in rows}


async def _member_names(s: AsyncSession, group_ids) -> dict:
    """{group_id: {user_id: 暱稱或帳號名}}：比對 @提及用的當下成員名單"""
    ids = {g for g in group_ids if g}
    if not ids:
        return {}
    rows = await s.execute(
        select(
            GroupMember.group_id, GroupMember.user_id, User.username, User.display_name
        )
        .join(User, User.user_id == GroupMember.user_id)
        .where(GroupMember.group_id.in_(ids))
    )
    grouped: dict = {}
    for group_id, user_id, username, display_name in rows:
        grouped.setdefault(group_id, {})[user_id] = display_name or username
    return grouped


async def _reactions_by_message(s: AsyncSession, message_ids) -> dict:
    ids = list(message_ids)
    if not ids:
        return {}
    rows = await s.execute(
        select(
            GroupMessageReaction.message_id,
            GroupMessageReaction.user_id,
            GroupMessageReaction.reaction,
        )
        .where(GroupMessageReaction.message_id.in_(ids))
        .order_by(GroupMessageReaction.created_at, GroupMessageReaction.id)
    )
    grouped: dict = {}
    for message_id, user_id, reaction in rows:
        grouped.setdefault(message_id, []).append(
            {"user_id": user_id, "reaction": reaction}
        )
    return grouped


async def _render(
    s: AsyncSession, msgs: List[GroupMessage], first_visible: int
) -> List[dict]:
    """訊息 → 輸出格式，補發送者名稱、回覆預覽（看不到的目標不給預覽）、表情"""
    parent_ids = {m.reply_to_message_id for m in msgs if m.reply_to_message_id}
    parents = {}
    if parent_ids:
        rows = await s.execute(
            select(GroupMessage).where(
                GroupMessage.id.in_(parent_ids), GroupMessage.id > first_visible
            )
        )
        parents = {p.id: p for p in rows.scalars()}
    names = await _names(
        s, [m.from_user_id for m in msgs] + [p.from_user_id for p in parents.values()]
    )
    reactions = await _reactions_by_message(s, [m.id for m in msgs])
    system_names = await display_names(
        s, [system_user_id(m.content) for m in msgs if m.message_type == "system"]
    )
    member_names = await _member_names(
        s, [m.group_id for m in msgs if m.message_type == "text"]
    )
    result = []
    for m in msgs:
        username, display_name = names.get(m.from_user_id, (None, None))
        item = message_dict(m, username, display_name)
        parent = parents.get(m.reply_to_message_id)
        if parent is not None:
            p_username, p_display = names.get(parent.from_user_id, (None, None))
            item["reply_to"] = build_reply_preview(
                parent.id,
                parent.from_user_id,
                p_display,
                p_username,
                parent.content,
                parent.message_type,
            )
        item["reactions"] = reactions.get(m.id, [])
        if m.message_type == "text":
            item["mentions"] = find_mentions(
                m.content or "", member_names.get(m.group_id, {})
            )
        if m.message_type == "system":
            uid = system_user_id(m.content)
            item["system_name"] = system_names.get(uid, uid) if uid else None
        result.append(item)
    return result


async def _visible_message(
    s: AsyncSession, message_id: int, user_id: str, lock: bool = False
):
    """(訊息, 成員列)；不是成員或看不到就 (None, None)"""
    stmt = select(GroupMessage).where(GroupMessage.id == message_id)
    if lock:
        stmt = stmt.with_for_update()
    msg = (await s.execute(stmt)).scalar_one_or_none()
    if msg is None:
        return None, None
    member = await is_member(s, msg.group_id, user_id)
    if member is None or msg.id <= member.first_visible_message_id:
        return None, None
    return msg, member


class GroupMessagesRepository:
    async def send_message(
        self,
        group_id: int,
        user_id: str,
        content: str,
        reply_to_message_id: Optional[int] = None,
        message_type: str = "text",
        session: AsyncSession | None = None,
    ) -> dict:
        text = (content or "").strip()
        if not text:
            return {"success": False, "error": "empty_content"}
        async with using_session(session) as s:
            member = await is_member(s, group_id, user_id)
            if member is None:
                return {"success": False, "error": "not_found"}
            if message_type == CARD_TYPE:
                # AI 分析卡片不受一般訊息的字數上限管（內容由伺服器取自 AI 回答，見 core/ai_card.py）
                max_length = CARD_MAX_LENGTH
            else:
                max_length = await config_repo.get_config(
                    "limit_message_max_length", DEFAULT_MAX_LENGTH, session=s
                )
                max_length = (
                    int(max_length) if max_length is not None else DEFAULT_MAX_LENGTH
                )
            if len(text) > max_length:
                return {
                    "success": False,
                    "error": "message_too_long",
                    "max_length": max_length,
                }
            if reply_to_message_id is not None:
                target, _ = await _visible_message(s, reply_to_message_id, user_id)
                if (
                    target is None
                    or target.group_id != group_id
                    or target.message_type == "system"
                ):
                    return {"success": False, "error": "reply_target_not_found"}
                if target.message_type == "recalled":
                    return {"success": False, "error": "reply_target_recalled"}

            msg = GroupMessage(
                group_id=group_id,
                from_user_id=user_id,
                content=text,
                message_type=message_type,
                reply_to_message_id=reply_to_message_id,
            )
            s.add(msg)
            await s.flush()
            await s.refresh(msg)
            await s.execute(
                update(GroupChat)
                .where(GroupChat.id == group_id)
                .values(last_message_id=msg.id, last_message_at=msg.created_at)
            )
            # 自己送的算已讀（未讀數不算自己的，讀取位置也跟著往前）
            await s.execute(
                update(GroupMember)
                .where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
                .values(
                    last_read_message_id=func.greatest(
                        GroupMember.last_read_message_id, msg.id
                    )
                )
            )
            (message,) = await _render(s, [msg], member.first_visible_message_id)
            group_name = (
                await s.execute(select(GroupChat.name).where(GroupChat.id == group_id))
            ).scalar_one()
            return {
                "success": True,
                "message": message,
                "group_name": group_name,  # 通知標題用
                "member_ids": await _member_ids(s, group_id),
                # 被 @ 的成員（不含自己）：關了通知也要通知
                "mentioned_ids": [
                    m["user_id"] for m in message["mentions"] if m["user_id"] != user_id
                ],
            }

    async def get_messages(
        self,
        group_id: int,
        user_id: str,
        limit: int = 50,
        before_id: Optional[int] = None,
        session: AsyncSession | None = None,
    ) -> dict:
        async with using_session(session) as s:
            member = await is_member(s, group_id, user_id)
            if member is None:
                return {"success": False, "error": "not_found"}
            stmt = select(GroupMessage).where(
                GroupMessage.group_id == group_id,
                GroupMessage.id > member.first_visible_message_id,
            )
            if before_id is not None:
                stmt = stmt.where(GroupMessage.id < before_id)
            rows = list(
                (
                    await s.execute(stmt.order_by(GroupMessage.id.desc()).limit(limit))
                ).scalars()
            )
            rows.reverse()
            return {
                "success": True,
                "messages": await _render(s, rows, member.first_visible_message_id),
                "has_more": len(rows) == limit,
            }

    async def mark_read(
        self,
        group_id: int,
        user_id: str,
        last_message_id: int,
        session: AsyncSession | None = None,
    ) -> dict:
        """讀到哪：只增不減、不超過群組最新一則。changed 給 router 決定要不要推播"""
        async with using_session(session) as s:
            member = await is_member(s, group_id, user_id)
            if member is None:
                return {"success": False, "error": "not_found"}
            latest = (
                await s.execute(
                    select(func.coalesce(func.max(GroupMessage.id), 0)).where(
                        GroupMessage.group_id == group_id
                    )
                )
            ).scalar_one()
            # 不低於可見範圍（看不到的訊息不算讀過，別人的「已讀 N」才不會算錯）
            new_value = max(
                member.last_read_message_id,
                member.first_visible_message_id,
                min(int(last_message_id), latest),
            )
            changed = new_value != member.last_read_message_id
            if changed:
                await s.execute(
                    update(GroupMember)
                    .where(
                        GroupMember.group_id == group_id, GroupMember.user_id == user_id
                    )
                    .values(
                        last_read_message_id=func.greatest(
                            GroupMember.last_read_message_id, new_value
                        )
                    )
                )
            return {
                "success": True,
                "changed": changed,
                "last_read_message_id": new_value,
                "member_ids": await _member_ids(s, group_id),
            }

    async def recall_message(
        self,
        message_id: int,
        user_id: str,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> dict:
        async with using_session(session) as s:
            msg, _ = await _visible_message(s, message_id, user_id, lock=True)
            if msg is None:
                return {"success": False, "error": "message_not_found"}
            if msg.from_user_id != user_id or msg.message_type == "system":
                return {"success": False, "error": "permission_denied"}
            if msg.message_type == "recalled":
                return {"success": False, "error": "already_recalled"}
            now = now or datetime.now(timezone.utc)
            if msg.created_at and now - msg.created_at > RECALL_WINDOW:
                return {"success": False, "error": "recall_window_expired"}
            msg.content = ""
            msg.message_type = "recalled"
            await s.execute(
                delete(GroupMessageReaction).where(
                    GroupMessageReaction.message_id == msg.id
                )
            )
            await s.flush()
            # AI 助理整理過這則的問答也要作廢（同一交易）
            await chat_assistant_history_repo.invalidate_message(
                "group", msg.id, session=s
            )
            return {
                "success": True,
                "group_id": msg.group_id,
                "message_id": msg.id,
                "member_ids": await _member_ids(s, msg.group_id),
            }

    async def _locked_reactable(self, s: AsyncSession, message_id: int, user_id: str):
        """鎖訊息列（跟收回排隊，收回後不會再被插回表情）"""
        msg, _ = await _visible_message(s, message_id, user_id, lock=True)
        if msg is None or msg.message_type == "system":
            return None, "message_not_found"
        if msg.message_type == "recalled":
            return None, "message_recalled"
        return msg, None

    async def _reaction_result(self, s: AsyncSession, msg: GroupMessage) -> dict:
        grouped = await _reactions_by_message(s, [msg.id])
        return {
            "success": True,
            "group_id": msg.group_id,
            "message_id": msg.id,
            "reactions": grouped.get(msg.id, []),
            "member_ids": await _member_ids(s, msg.group_id),
        }

    async def set_reaction(
        self,
        message_id: int,
        user_id: str,
        reaction: str,
        session: AsyncSession | None = None,
    ) -> dict:
        if reaction not in REACTION_KEYS:
            return {"success": False, "error": "invalid_reaction"}
        async with using_session(session) as s:
            msg, error = await self._locked_reactable(s, message_id, user_id)
            if error:
                return {"success": False, "error": error}
            await s.execute(
                pg_insert(GroupMessageReaction)
                .values(message_id=msg.id, user_id=user_id, reaction=reaction)
                .on_conflict_do_update(
                    constraint="uq_group_message_reaction",
                    set_={"reaction": reaction, "created_at": func.now()},
                )
            )
            return await self._reaction_result(s, msg)

    async def remove_reaction(
        self, message_id: int, user_id: str, session: AsyncSession | None = None
    ) -> dict:
        async with using_session(session) as s:
            msg, error = await self._locked_reactable(s, message_id, user_id)
            if error:
                return {"success": False, "error": error}
            deleted = await s.execute(
                delete(GroupMessageReaction).where(
                    GroupMessageReaction.message_id == msg.id,
                    GroupMessageReaction.user_id == user_id,
                )
            )
            return {
                **await self._reaction_result(s, msg),
                "changed": deleted.rowcount > 0,
            }

    async def report_message(
        self,
        message_id: int,
        reporter_id: str,
        reason: str,
        note: Optional[str] = None,
        session: AsyncSession | None = None,
    ) -> dict:
        """快照（被檢舉那則＋前 10 則，不含系統訊息、不越過檢舉者的可見範圍）由這裡撈，不收前端傳的內容"""
        if reason not in REPORT_REASONS:
            return {"success": False, "error": "invalid_reason"}
        async with using_session(session) as s:
            msg, member = await _visible_message(s, message_id, reporter_id)
            if msg is None or msg.message_type == "system":
                return {"success": False, "error": "message_not_found"}
            if msg.from_user_id == reporter_id:
                return {"success": False, "error": "cannot_report_self"}
            before = list(
                (
                    await s.execute(
                        select(GroupMessage)
                        .where(
                            GroupMessage.group_id == msg.group_id,
                            GroupMessage.id < msg.id,
                            GroupMessage.id > member.first_visible_message_id,
                            GroupMessage.message_type != "system",
                        )
                        .order_by(GroupMessage.id.desc())
                        .limit(SNAPSHOT_BEFORE)
                    )
                ).scalars()
            )
            snapshot = [
                {
                    "id": m.id,
                    "from_user_id": m.from_user_id,
                    "content": m.content,
                    "message_type": m.message_type,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in [*reversed(before), msg]
            ]
            report_id = (
                await s.execute(
                    pg_insert(GroupReport)
                    .values(
                        reporter_user_id=reporter_id,
                        reported_user_id=msg.from_user_id,
                        group_id=msg.group_id,
                        message_id=msg.id,
                        reason=reason,
                        note=(note or None),
                        snapshot=snapshot,
                    )
                    .on_conflict_do_nothing(constraint="uq_group_report")
                    .returning(GroupReport.id)
                )
            ).scalar_one_or_none()
            if report_id is None:
                return {"success": False, "error": "already_reported"}
            return {
                "success": True,
                "report_id": report_id,
                "group_id": msg.group_id,
                "reported_user_id": msg.from_user_id,
                "snapshot": snapshot,  # 給背景打風險分數；router 不回給前端
            }


group_messages_repo = GroupMessagesRepository()
