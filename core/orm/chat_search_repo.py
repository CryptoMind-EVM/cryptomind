"""
聊天搜尋：一個框找人、群組、訊息（GET /api/chat-search）。

只搜自己看得到的，可見範圍跟列表／聊天室一致：
- 私訊對話：自己是參與者、我沒封鎖對方（含互相封鎖，同 messages_repo.get_conversations）、
  還有至少一則沒被自己刪掉的訊息（整段「刪除對話」後不再出現）。
- 私訊訊息：在上述對話裡、沒被自己刪（dm_message_deletions）、沒收回。
- 群組訊息：目前是成員、id > first_visible_message_id、只搜 text（系統訊息的 content 是事件碼）。
- 聯絡人：好友 ∪ 看得到的私訊對象，排除任一方封鎖、排除自己。
每段都先從自己的對話／群組收斂再比對內容，並各自 LIMIT。唯讀，不寫 DB。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import and_, case, exists, func, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_card import CARD_TYPE

from .group_chat_repo import display_names
from .models import (
    DmConversation,
    DmMessage,
    DmMessageDeletion,
    Friendship,
    GroupChat,
    GroupMember,
    GroupMessage,
    User,
)
from .session import using_session

QUERY_MAX = 50
CONTACTS_LIMIT = 10
GROUPS_LIMIT = 10
MESSAGES_LIMIT = 30

SNIPPET_BEFORE = 30
SNIPPET_AFTER = 60
SNIPPET_MAX = 100
_ELLIPSIS = "…"
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _iso(val: Optional[datetime]) -> Optional[str]:
    return val.isoformat() if val else None


def like_pattern(query: str) -> str:
    """包成 %q%，q 裡的 \\ % _ 當一般字元（搭配 ilike(..., escape="\\")）"""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def make_snippet(
    content: str,
    query: str,
    before: int = SNIPPET_BEFORE,
    after: int = SNIPPET_AFTER,
    max_len: int = SNIPPET_MAX,
) -> str:
    """第一個命中（不分大小寫）附近的片段：前 ~before 字、後 ~after 字，被截掉的那側加「…」。

    片段本體不超過 max_len 字；命中本身比 max_len 長就只留命中的開頭。
    換行換成空白（列表一行顯示）。純文字，跳脫與標亮交給前端。
    """
    text = content or ""
    # 用 re 找位置：位置對應原字串（lower() 遇到少數字元會變長，位置會歪）
    m = re.search(re.escape(query), text, re.IGNORECASE) if query else None
    start_hit, end_hit = (m.start(), m.end()) if m else (0, 0)
    if end_hit - start_hit >= max_len:
        start, end = start_hit, start_hit + max_len
    else:
        start = start_hit - min(before, start_hit, max_len - (end_hit - start_hit))
        end = min(len(text), end_hit + after, start + max_len)
    window = re.sub(r"[\r\n\t]", " ", text[start:end])
    return (
        (_ELLIPSIS if start > 0 else "")
        + window
        + (_ELLIPSIS if end < len(text) else "")
    )


def _visible_conversations(user_id: str):
    """看得到的私訊對話（conv_id, other_id, last_message_at）"""
    other = case(
        (DmConversation.user1_id == user_id, DmConversation.user2_id),
        else_=DmConversation.user1_id,
    )
    # 我封鎖的人：我先封鎖的，或互相封鎖時我是後封鎖的那方（同 get_conversations）
    i_blocked = exists(
        select(Friendship.id).where(
            Friendship.status == "blocked",
            or_(
                and_(Friendship.user_id == user_id, Friendship.friend_id == other),
                and_(
                    Friendship.user_id == other,
                    Friendship.friend_id == user_id,
                    Friendship.mutual_block.is_(True),
                ),
            ),
        )
    ).correlate(DmConversation)
    has_message_for_me = (
        exists(
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
        )
    ).correlate(DmConversation)
    return (
        select(
            DmConversation.id.label("conv_id"),
            other.label("other_id"),
            DmConversation.last_message_at.label("last_message_at"),
        )
        .where(
            or_(DmConversation.user1_id == user_id, DmConversation.user2_id == user_id),
            ~i_blocked,
            has_message_for_me,
        )
        .cte("visible_convs")
    )


class ChatSearchRepository:
    async def search(
        self,
        user_id: str,
        query: str,
        include_groups: bool = True,
        session: AsyncSession | None = None,
    ) -> dict:
        """query 由呼叫端先 strip、驗長度；include_groups=False（群組開關關）時群組相關回空"""
        pattern = like_pattern(query)
        async with using_session(session) as s:
            contacts = await self._contacts(s, user_id, pattern)
            groups = await self._groups(s, user_id, pattern) if include_groups else []
            messages = await self._messages(s, user_id, query, pattern, include_groups)
        return {"contacts": contacts, "groups": groups, "messages": messages}

    async def _contacts(
        self, s: AsyncSession, user_id: str, pattern: str
    ) -> List[dict]:
        vc = _visible_conversations(user_id)
        friend_ids = select(
            case(
                (Friendship.user_id == user_id, Friendship.friend_id),
                else_=Friendship.user_id,
            ).label("uid")
        ).where(
            Friendship.status == "accepted",
            or_(Friendship.user_id == user_id, Friendship.friend_id == user_id),
        )
        candidates = union(friend_ids, select(vc.c.other_id.label("uid"))).subquery()
        # 任一方封鎖都不列（一對人只有一列，status=blocked 就是有人封鎖）
        blocked = exists(
            select(Friendship.id).where(
                Friendship.status == "blocked",
                or_(
                    and_(
                        Friendship.user_id == user_id,
                        Friendship.friend_id == User.user_id,
                    ),
                    and_(
                        Friendship.user_id == User.user_id,
                        Friendship.friend_id == user_id,
                    ),
                ),
            )
        ).correlate(User)
        stmt = (
            select(User.user_id, User.username, User.display_name, vc.c.conv_id)
            .join(candidates, candidates.c.uid == User.user_id)
            .outerjoin(vc, vc.c.other_id == User.user_id)
            .where(
                User.user_id != user_id,
                ~blocked,
                or_(
                    User.username.ilike(pattern, escape="\\"),
                    User.display_name.ilike(pattern, escape="\\"),
                ),
            )
            # 最近聊過的排前面，其餘照名字
            .order_by(
                vc.c.last_message_at.desc().nullslast(),
                func.lower(func.coalesce(User.display_name, User.username)),
                User.user_id,
            )
            .limit(CONTACTS_LIMIT)
        )
        rows = (await s.execute(stmt)).all()
        return [
            {
                "user_id": uid,
                "username": username,
                "display_name": display,
                "conversation_id": conv_id,
            }
            for uid, username, display, conv_id in rows
        ]

    async def _groups(self, s: AsyncSession, user_id: str, pattern: str) -> List[dict]:
        me = GroupMember.__table__.alias("me")
        count_sq = (
            select(func.count())
            .select_from(GroupMember)
            .where(GroupMember.group_id == GroupChat.id)
            .correlate(GroupChat)
            .scalar_subquery()
        )
        stmt = (
            select(GroupChat.id, GroupChat.name, count_sq)
            .join(me, and_(me.c.group_id == GroupChat.id, me.c.user_id == user_id))
            .where(GroupChat.name.ilike(pattern, escape="\\"))
            .order_by(
                func.coalesce(GroupChat.last_message_at, GroupChat.created_at).desc(),
                GroupChat.id.desc(),
            )
            .limit(GROUPS_LIMIT)
        )
        rows = (await s.execute(stmt)).all()
        return [
            {"id": gid, "name": name, "member_count": count}
            for gid, name, count in rows
        ]

    async def _messages(
        self,
        s: AsyncSession,
        user_id: str,
        query: str,
        pattern: str,
        include_groups: bool,
    ) -> List[dict]:
        vc = _visible_conversations(user_id)
        dm_rows = (
            await s.execute(
                select(
                    DmMessage.id,
                    DmMessage.conversation_id,
                    vc.c.other_id,
                    DmMessage.from_user_id,
                    DmMessage.content,
                    DmMessage.created_at,
                )
                .join(vc, vc.c.conv_id == DmMessage.conversation_id)
                .outerjoin(
                    DmMessageDeletion,
                    and_(
                        DmMessageDeletion.message_id == DmMessage.id,
                        DmMessageDeletion.user_id == user_id,
                    ),
                )
                .where(
                    DmMessageDeletion.id.is_(None),
                    DmMessage.message_type.is_distinct_from("recalled"),
                    DmMessage.content.ilike(pattern, escape="\\"),
                )
                .order_by(DmMessage.created_at.desc(), DmMessage.id.desc())
                .limit(MESSAGES_LIMIT)
            )
        ).all()
        items = [
            {
                "kind": "dm",
                "message_id": mid,
                "conversation_id": conv_id,
                "group_id": None,
                "other_user_id": other_id,
                "chat_name": None,
                "from_user_id": from_id,
                "content": content,
                "created_at": created_at,
            }
            for mid, conv_id, other_id, from_id, content, created_at in dm_rows
        ]

        if include_groups:
            me = GroupMember.__table__.alias("me")
            group_rows = (
                await s.execute(
                    select(
                        GroupMessage.id,
                        GroupMessage.group_id,
                        GroupChat.name,
                        GroupMessage.from_user_id,
                        GroupMessage.content,
                        GroupMessage.created_at,
                    )
                    .join(
                        me,
                        and_(
                            me.c.group_id == GroupMessage.group_id,
                            me.c.user_id == user_id,
                        ),
                    )
                    .join(GroupChat, GroupChat.id == GroupMessage.group_id)
                    .where(
                        GroupMessage.id > me.c.first_visible_message_id,
                        GroupMessage.message_type.in_(("text", CARD_TYPE)),
                        GroupMessage.content.ilike(pattern, escape="\\"),
                    )
                    .order_by(GroupMessage.created_at.desc(), GroupMessage.id.desc())
                    .limit(MESSAGES_LIMIT)
                )
            ).all()
            items += [
                {
                    "kind": "group",
                    "message_id": mid,
                    "conversation_id": None,
                    "group_id": gid,
                    "other_user_id": None,
                    "chat_name": name,
                    "from_user_id": from_id,
                    "content": content,
                    "created_at": created_at,
                }
                for mid, gid, name, from_id, content, created_at in group_rows
            ]

        items.sort(
            key=lambda m: (m["created_at"] or _EPOCH, m["message_id"]), reverse=True
        )
        items = items[:MESSAGES_LIMIT]
        names = await display_names(
            s,
            [m["from_user_id"] for m in items] + [m["other_user_id"] for m in items],
        )
        return [
            {
                "kind": m["kind"],
                "message_id": m["message_id"],
                "conversation_id": m["conversation_id"],
                "group_id": m["group_id"],
                "other_user_id": m["other_user_id"],
                "chat_name": m["chat_name"]
                if m["kind"] == "group"
                else names.get(m["other_user_id"], m["other_user_id"]),
                "from_user_id": m["from_user_id"],
                # 群組發送者帳號被刪時 from_user_id 是 NULL
                "from_name": names.get(m["from_user_id"], m["from_user_id"] or ""),
                "snippet": make_snippet(m["content"], query),
                "created_at": _iso(m["created_at"]),
            }
            for m in items
        ]


chat_search_repo = ChatSearchRepository()
