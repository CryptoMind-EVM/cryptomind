"""
聊天室 AI 助理：撈「提問者自己看得到的」訊息、每日次數（設計 docs/plans/2026-10-01-chat-assistant-design.md）。

- 私訊：對話參與者才撈得到；排除自己刪除（只對自己隱藏）的、收回的。
- 群組：成員才撈得到；只到 first_visible_message_id 之後（入群前看不到，群主開歷史才有更早的）；
  系統訊息（事件碼）轉成英文一行。
- unread：群組用 from_id（打開前的 last_read+1）；私訊用 unread_count（打開前的未讀數，從對方的來訊往回數）。
  兩個都是前端給的，只當下限，一樣套可見範圍。
- 回傳不帶 user id：給模型的只有名字。
- 次數記在既有 tool_usage_log（tool_id='chat_assistant'），不新增表。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_card import CARD_TYPE

from .group_chat_repo import display_names, is_member
from .models import (
    DmConversation,
    DmMessage,
    DmMessageDeletion,
    GroupMessage,
    ToolUsageLog,
)
from .session import using_session

TOOL_ID = "chat_assistant"
RANGES = ("recent", "unread", "24h", "3d", "message")
RECENT_MAX_MESSAGES = 300
RANGE_MAX_MESSAGES = 1500  # 長範圍只取最新這麼多則（再由上下文層切段濃縮）
AROUND = 5  # 「問 AI」單則：前後各幾則
_WINDOWS = {"24h": timedelta(hours=24), "3d": timedelta(days=3)}

_SYSTEM_TEXT = {
    "created": "{name} created the group",
    "member_joined": "{name} joined the group",
    "member_left": "{name} left the group",
    "member_removed": "{name} was removed from the group",
    "owner_changed": "{name} is now the group owner",
}

_NOT_FOUND = {"success": False, "error": "not_found"}


def _system_text(content: Optional[str], names: dict) -> str:
    """事件碼 → 英文一行（模型看得懂就好；名字查不到寫 someone，不露 user id）"""
    code, _, arg = (content or "").partition(":")
    if code in _SYSTEM_TEXT:
        return _SYSTEM_TEXT[code].format(name=names.get(arg) or "someone")
    if code == "renamed":
        return f"Group renamed to {arg}"
    if code == "history_visible":
        return "History for new members turned " + ("on" if arg == "on" else "off")
    return "(group event)"


async def _window(s: AsyncSession, base, model, range_, from_id, around_id, now):
    """依範圍取訊息（由舊到新）；around_id 看不到回 None"""
    if range_ == "message":
        if (
            await s.execute(base.where(model.id == around_id))
        ).scalar_one_or_none() is None:
            return None
        before = (
            await s.execute(
                base.where(model.id <= around_id)
                .order_by(model.id.desc())
                .limit(AROUND + 1)
            )
        ).scalars()
        after = (
            await s.execute(
                base.where(model.id > around_id).order_by(model.id).limit(AROUND)
            )
        ).scalars()
        return list(reversed(list(before))) + list(after)
    limit = RECENT_MAX_MESSAGES
    if range_ == "unread":
        base, limit = base.where(model.id >= (from_id or 0)), RANGE_MAX_MESSAGES
    elif range_ in _WINDOWS:
        base, limit = (
            base.where(model.created_at >= now - _WINDOWS[range_]),
            RANGE_MAX_MESSAGES,
        )
    rows = list(
        (await s.execute(base.order_by(model.id.desc()).limit(limit))).scalars()
    )
    rows.reverse()
    return rows


async def _nth_incoming_id(
    s: AsyncSession, base, model, user_id: str, count: int
) -> int:
    """私訊的「我沒看的」：打開前的未讀數 → 對方傳來的第 count 新那則的 id（不夠就從頭）"""
    if count <= 0:
        return 2**31  # 沒有未讀：什麼都不給
    row = await s.scalar(
        base.with_only_columns(model.id)
        .where(model.from_user_id != user_id)
        .order_by(model.id.desc())
        .offset(count - 1)
        .limit(1)
    )
    return row or 0


async def _lines(s: AsyncSession, rows, model, base) -> list[dict]:
    """訊息列 → [{id, name, text, type, created_at, reply_to_name}]。
    回覆對象也要過同一個可見條件（base）：被收回、自己刪了、入群前的 → 不給「回覆誰」"""
    parent_ids = {m.reply_to_message_id for m in rows if m.reply_to_message_id}
    parents = {}
    if parent_ids:
        result = await s.execute(
            base.with_only_columns(model.id, model.from_user_id).where(
                model.id.in_(parent_ids)
            )
        )
        parents = dict(result.all())
    system_ids = [
        m.content.partition(":")[2] for m in rows if m.message_type == "system"
    ]
    names = await display_names(
        s, [m.from_user_id for m in rows] + list(parents.values()) + system_ids
    )
    out = []
    for m in rows:
        if m.message_type == "system":
            line = {
                "name": None,
                "text": _system_text(m.content, names),
                "type": "system",
            }
        elif not (m.content or "").strip() or m.message_type == CARD_TYPE:
            # AI 分析卡片是 AI 自己的輸出（常常就是這個聊天室的整理），再餵回去只會重複、吃掉字數預算
            continue
        else:
            line = {
                "name": names.get(m.from_user_id) or "someone",
                "text": m.content,
                "type": "text",
            }
        parent_from = parents.get(m.reply_to_message_id)
        line.update(
            id=m.id,
            created_at=m.created_at,
            reply_to_name=(names.get(parent_from) or "someone")
            if parent_from
            else None,
        )
        out.append(line)
    return out


class ChatAssistantRepository:
    async def fetch_messages(
        self,
        kind: str,
        target_id: int,
        user_id: str,
        range_: str = "recent",
        *,
        from_id: Optional[int] = None,
        unread_count: Optional[int] = None,
        around_id: Optional[int] = None,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        async with using_session(session) as s:
            if kind == "dm":
                conv = await s.execute(
                    select(DmConversation.id).where(
                        DmConversation.id == target_id,
                        or_(
                            DmConversation.user1_id == user_id,
                            DmConversation.user2_id == user_id,
                        ),
                    )
                )
                if conv.scalar_one_or_none() is None:
                    return _NOT_FOUND
                model = DmMessage
                base = (
                    select(DmMessage)
                    .outerjoin(
                        DmMessageDeletion,
                        and_(
                            DmMessageDeletion.message_id == DmMessage.id,
                            DmMessageDeletion.user_id == user_id,
                        ),
                    )
                    .where(
                        DmMessage.conversation_id == target_id,
                        DmMessageDeletion.id.is_(None),
                        DmMessage.message_type != "recalled",
                    )
                )
            elif kind == "group":
                member = await is_member(s, target_id, user_id)
                if member is None:
                    return _NOT_FOUND
                model = GroupMessage
                floor = member.first_visible_message_id
                base = select(GroupMessage).where(
                    GroupMessage.group_id == target_id,
                    GroupMessage.id > floor,
                    GroupMessage.message_type != "recalled",
                )
            else:
                return _NOT_FOUND
            if range_ == "unread" and from_id is None:
                from_id = await _nth_incoming_id(
                    s, base, model, user_id, unread_count or 0
                )
            rows = await _window(s, base, model, range_, from_id, around_id, now)
            if rows is None:
                return _NOT_FOUND
            return {
                "success": True,
                "messages": await _lines(s, rows, model, base),
            }

    async def consume_quota(
        self, user_id: str, limit: Optional[int], session: AsyncSession | None = None
    ) -> dict:
        """今天用一次。limit=None 只記不擋；有上限時原子 upsert，滿了不加、回 ok=False"""
        if limit is not None and limit <= 0:
            return {"ok": False, "used": 0, "remaining": 0}
        stmt = pg_insert(ToolUsageLog).values(
            user_id=user_id,
            tool_id=TOOL_ID,
            used_date=func.current_date(),
            call_count=1,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "tool_id", "used_date"],
            set_={"call_count": ToolUsageLog.call_count + 1},
            where=None if limit is None else ToolUsageLog.call_count < limit,
        ).returning(ToolUsageLog.call_count)
        async with using_session(session) as s:
            row = (await s.execute(stmt)).first()
        if row is None:
            return {"ok": False, "used": limit, "remaining": 0}
        used = row[0]
        return {
            "ok": True,
            "used": used,
            "remaining": None if limit is None else max(0, limit - used),
        }

    async def refund_quota(
        self, user_id: str, session: AsyncSession | None = None
    ) -> None:
        """失敗（排隊逾時、模型錯誤）退回今天的一次；不會變負數"""
        async with using_session(session) as s:
            await s.execute(
                update(ToolUsageLog)
                .where(
                    ToolUsageLog.user_id == user_id,
                    ToolUsageLog.tool_id == TOOL_ID,
                    ToolUsageLog.used_date == func.current_date(),
                    ToolUsageLog.call_count > 0,
                )
                .values(call_count=ToolUsageLog.call_count - 1)
            )

    async def used_today(
        self, user_id: str, session: AsyncSession | None = None
    ) -> int:
        async with using_session(session) as s:
            used = await s.scalar(
                select(ToolUsageLog.call_count).where(
                    ToolUsageLog.user_id == user_id,
                    ToolUsageLog.tool_id == TOOL_ID,
                    ToolUsageLog.used_date == func.current_date(),
                )
            )
        return int(used or 0)


chat_assistant_repo = ChatAssistantRepository()
