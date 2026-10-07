"""
群組聊天：群組、成員、邀請、系統訊息（c066，設計 docs/plans/2026-10-01-group-chat-design.md）。

- 只有群主、沒有管理員；群主可手動轉讓給 Pro 成員；群主退群 → 最早入群的人接手；最後一人退群 → 刪群。
- 群主解散（c068）：成員全部移出、待處理邀請取消、dissolved_at 有值、owner_id 設 NULL（不佔開群上限）；
  訊息留著當檢舉證據，所以不刪群。解散的群對所有讀取路徑都等於不存在（大多靠成員資格自然排除）。
- 新成員看得到 id > first_visible_message_id 的訊息：群主開 history_visible 才從 0 開始。
- 邀請只能邀自己的好友、對方要是 Pro、雙方沒封鎖；對方接受才進群。
- 接受邀請鎖群組列（FOR UPDATE），兩人同時搶最後一個位子只進得去一個。
  鎖的順序一律先群組列、再邀請列（同開邀請、解散、最後一人退群），不然會互相卡死。
- 系統訊息 content 是事件碼（created:<uid>、member_joined:<uid>、renamed:<名稱>…），前端用 i18n 組字。
訊息相關（送、讀、收回、表情、檢舉）在 group_messages_repo。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_card import CARD_TYPE, card_preview

from .chat_assistant_history_repo import chat_assistant_history_repo
from .config_repo import config_repo
from .models import (
    ChatOrder,
    ChatPin,
    Friendship,
    GroupChat,
    GroupInvite,
    GroupMember,
    GroupMessage,
    User,
)
from .session import using_session

NAME_MAX = 30
DEFAULT_LIMITS = {
    "limit_group_create": 5,
    "limit_group_join": 20,
    "limit_group_members": 50,
    "limit_daily_group_invites": 30,
}
MAX_INVITEES_PER_REQUEST = 50
PREMIUM_TIERS = (
    "premium",
    "plus",
    "pro",
)  # 同 core.database.user._normalize_membership_tier


def _iso(val: Optional[datetime]) -> Optional[str]:
    return val.isoformat() if val else None


def premium_clause():
    """SQL 版的「現在是 Pro」：tier 正規化後是 premium，而且沒過期（同 get_user_membership）"""
    tier = func.lower(func.trim(func.coalesce(User.membership_tier, "free")))
    return and_(
        tier.in_(PREMIUM_TIERS),
        or_(
            User.membership_expires_at.is_(None),
            User.membership_expires_at > func.now(),
        ),
    )


def message_dict(
    msg: GroupMessage,
    username: Optional[str] = None,
    display_name: Optional[str] = None,
) -> dict:
    """群組訊息輸出格式：與私訊同形（前端共用氣泡、回覆、表情元件）；reply_to／reactions 由呼叫端補"""
    return {
        "id": msg.id,
        "group_id": msg.group_id,
        "from_user_id": msg.from_user_id,
        "from_username": username,
        "from_display_name": display_name,
        "content": msg.content,
        "message_type": msg.message_type,
        "reply_to_message_id": msg.reply_to_message_id,
        "reply_to": None,
        "reactions": [],
        "created_at": _iso(msg.created_at),
        "system_name": None,  # 系統訊息當事人的名字（system_user_id 有值時由呼叫端補）
        "mentions": [],  # @提及的成員 [{user_id, name}]（group_messages_repo._render 補）
    }


# 系統訊息裡「誰」的事件碼：content 是 <code>:<user_id>
SYSTEM_USER_CODES = (
    "created",
    "member_joined",
    "member_left",
    "member_removed",
    "owner_changed",
    "dissolved",
)


def system_user_id(content: Optional[str]) -> Optional[str]:
    code, _, arg = (content or "").partition(":")
    return arg or None if code in SYSTEM_USER_CODES else None


async def display_names(s: AsyncSession, user_ids) -> dict:
    """{user_id: 暱稱或帳號名}（已退群的人也查得到，系統訊息才不會只剩 id）"""
    ids = {u for u in user_ids if u}
    if not ids:
        return {}
    rows = await s.execute(
        select(User.user_id, User.username, User.display_name).where(
            User.user_id.in_(ids)
        )
    )
    return {uid: display or username or uid for uid, username, display in rows}


async def add_system_message(s: AsyncSession, group_id: int, content: str) -> dict:
    """系統訊息（沒有發送者）：同時更新群組的最後一則"""
    msg = GroupMessage(
        group_id=group_id, from_user_id=None, content=content, message_type="system"
    )
    s.add(msg)
    await s.flush()
    await s.refresh(msg)
    await s.execute(
        update(GroupChat)
        .where(GroupChat.id == group_id)
        .values(last_message_id=msg.id, last_message_at=msg.created_at)
    )
    item = message_dict(msg)
    uid = system_user_id(content)
    if uid:
        item["system_name"] = (await display_names(s, [uid])).get(uid, uid)
    return item


async def is_member(
    s: AsyncSession, group_id: int, user_id: str
) -> Optional[GroupMember]:
    return (
        await s.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()


async def _limit(s: AsyncSession, key: str) -> int:
    value = await config_repo.get_config(key, DEFAULT_LIMITS[key], session=s)
    return int(value) if value is not None else DEFAULT_LIMITS[key]


async def _lock_user(s: AsyncSession, user_id: str) -> None:
    """同一個人的開群／入群排隊（算加入數時不會兩條路同時通過）。交易結束自動釋放"""
    await s.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"group-user:{user_id}")))
    )


async def _joined_count(s: AsyncSession, user_id: str) -> int:
    return (
        await s.execute(
            select(func.count())
            .select_from(GroupMember)
            .where(GroupMember.user_id == user_id)
        )
    ).scalar_one()


async def _member_count(s: AsyncSession, group_id: int) -> int:
    return (
        await s.execute(
            select(func.count())
            .select_from(GroupMember)
            .where(GroupMember.group_id == group_id)
        )
    ).scalar_one()


async def _member_ids(s: AsyncSession, group_id: int) -> List[str]:
    rows = await s.execute(
        select(GroupMember.user_id)
        .where(GroupMember.group_id == group_id)
        .order_by(GroupMember.joined_at, GroupMember.user_id)
    )
    return [r[0] for r in rows]


def _clean_name(name: Optional[str]) -> Optional[str]:
    cleaned = (name or "").strip()
    return cleaned if 1 <= len(cleaned) <= NAME_MAX else None


def _group_summary(group: GroupChat, member_count: int) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "owner_id": group.owner_id,
        "history_visible": group.history_visible,
        "member_count": member_count,
        "created_at": _iso(group.created_at),
    }


def _pair(a: str, b: str):
    return or_(
        and_(Friendship.user_id == a, Friendship.friend_id == b),
        and_(Friendship.user_id == b, Friendship.friend_id == a),
    )


class GroupChatRepository:
    # ── 開群／讀取 ────────────────────────────────────────

    async def create_group(
        self, owner_id: str, name: str, session: AsyncSession | None = None
    ) -> dict:
        cleaned = _clean_name(name)
        if cleaned is None:
            return {"success": False, "error": "invalid_name"}
        async with using_session(session) as s:
            await _lock_user(s, owner_id)
            owned = (
                await s.execute(
                    select(func.count())
                    .select_from(GroupChat)
                    .where(
                        GroupChat.owner_id == owner_id,
                        GroupChat.dissolved_at.is_(None),  # 解散的不佔名額（c068）
                    )
                )
            ).scalar_one()
            if owned >= await _limit(s, "limit_group_create"):
                return {"success": False, "error": "create_limit_reached"}
            if await _joined_count(s, owner_id) >= await _limit(s, "limit_group_join"):
                return {"success": False, "error": "join_limit_reached"}
            group = GroupChat(name=cleaned, owner_id=owner_id)
            s.add(group)
            await s.flush()
            s.add(
                GroupMember(
                    group_id=group.id, user_id=owner_id, first_visible_message_id=0
                )
            )
            await s.flush()
            system_message = await add_system_message(
                s, group.id, f"created:{owner_id}"
            )
            await s.refresh(group)
            return {
                "success": True,
                "group": _group_summary(group, 1),
                "system_message": system_message,
            }

    async def member_ids(
        self, group_id: int, session: AsyncSession | None = None
    ) -> List[str]:
        async with using_session(session) as s:
            return await _member_ids(s, group_id)

    async def unmuted_member_ids(
        self, group_id: int, session: AsyncSession | None = None
    ) -> List[str]:
        """新訊息要發通知的人（關了這群通知的不算）"""
        async with using_session(session) as s:
            rows = await s.execute(
                select(GroupMember.user_id).where(
                    GroupMember.group_id == group_id, GroupMember.muted.is_(False)
                )
            )
            return [r[0] for r in rows]

    async def list_groups(
        self, user_id: str, session: AsyncSession | None = None
    ) -> List[dict]:
        """我的群組（依最後動態排）：未讀＝看得到、比讀到的新、不是自己發的、不是系統訊息"""
        me = GroupMember.__table__.alias("me")
        count_sq = (
            select(func.count())
            .select_from(GroupMember)
            .where(GroupMember.group_id == GroupChat.id)
            .correlate(GroupChat)
            .scalar_subquery()
        )
        unread_sq = (
            select(func.count())
            .select_from(GroupMessage)
            .where(
                GroupMessage.group_id == GroupChat.id,
                GroupMessage.id > me.c.last_read_message_id,
                GroupMessage.id > me.c.first_visible_message_id,
                GroupMessage.message_type != "system",
                GroupMessage.from_user_id != user_id,
            )
            .correlate(GroupChat, me)
            .scalar_subquery()
        )
        last = GroupMessage.__table__.alias("last")
        lu = User.__table__.alias("lu")
        stmt = (
            select(
                GroupChat,
                me.c.muted,
                me.c.first_visible_message_id,
                count_sq.label("member_count"),
                unread_sq.label("unread_count"),
                last.c.id,
                last.c.content,
                last.c.message_type,
                last.c.from_user_id,
                lu.c.username,
                lu.c.display_name,
                ChatPin.position,
                ChatOrder.position,
            )
            .join(me, and_(me.c.group_id == GroupChat.id, me.c.user_id == user_id))
            .outerjoin(last, last.c.id == GroupChat.last_message_id)
            .outerjoin(lu, lu.c.user_id == last.c.from_user_id)
            .outerjoin(
                ChatPin,
                and_(
                    ChatPin.user_id == user_id,
                    ChatPin.kind == "group",
                    ChatPin.target_id == GroupChat.id,
                ),
            )
            .outerjoin(
                ChatOrder,
                and_(
                    ChatOrder.user_id == user_id,
                    ChatOrder.kind == "group",
                    ChatOrder.target_id == GroupChat.id,
                ),
            )
            .order_by(
                func.coalesce(GroupChat.last_message_at, GroupChat.created_at).desc(),
                GroupChat.id.desc(),
            )
        )
        async with using_session(session) as s:
            rows = (await s.execute(stmt)).all()
            # 最後一則是系統訊息（某人加入／退出…）：預覽要寫名字
            names = await display_names(
                s, [system_user_id(r[6]) for r in rows if r[7] == "system"]
            )
        result = []
        for (
            group,
            muted,
            first_visible,
            member_count,
            unread_count,
            last_id,
            last_content,
            last_type,
            last_from,
            last_username,
            last_display,
            pin_position,
            order_position,
        ) in rows:
            visible = last_id is not None and last_id > first_visible
            result.append(
                {
                    **_group_summary(group, member_count),
                    "muted": muted,
                    "unread_count": unread_count,
                    "pin_position": pin_position,  # 置頂順序（c067），沒置頂是 None
                    "order_position": order_position,  # 自訂順序（c068），還沒排過是 None
                    "last_message_at": _iso(group.last_message_at),
                    "last_message": (
                        {
                            "id": last_id,
                            # 卡片整篇不送進列表，只給一行預覽
                            "content": card_preview(last_content, 100)
                            if last_type == CARD_TYPE
                            else last_content,
                            "message_type": last_type,
                            "from_user_id": last_from,
                            "from_username": last_username,
                            "from_display_name": last_display,
                            "system_name": names.get(system_user_id(last_content))
                            if last_type == "system"
                            else None,
                        }
                        if visible
                        else None
                    ),
                }
            )
        return result

    async def get_group(
        self, group_id: int, user_id: str, session: AsyncSession | None = None
    ) -> Optional[dict]:
        """群組資訊＋成員（含每人讀到哪：前端算「已讀 N」與誰讀過）。非成員回 None"""
        async with using_session(session) as s:
            me = await is_member(s, group_id, user_id)
            if me is None:
                return None
            group = await s.get(GroupChat, group_id)
            rows = (
                await s.execute(
                    select(GroupMember, User.username, User.display_name)
                    .join(User, User.user_id == GroupMember.user_id)
                    .where(GroupMember.group_id == group_id)
                    .order_by(GroupMember.joined_at, GroupMember.user_id)
                )
            ).all()
            members = [
                {
                    "user_id": m.user_id,
                    "username": username,
                    "display_name": display_name,
                    "is_owner": m.user_id == group.owner_id,
                    "joined_at": _iso(m.joined_at),
                    "first_visible_message_id": m.first_visible_message_id,
                    "last_read_message_id": m.last_read_message_id,
                }
                for m, username, display_name in rows
            ]
            return {
                **_group_summary(group, len(members)),
                "members": members,
                "me": {
                    "muted": me.muted,
                    "first_visible_message_id": me.first_visible_message_id,
                    "last_read_message_id": me.last_read_message_id,
                },
            }

    # ── 群主操作 ──────────────────────────────────────────

    async def _locked_group_for_member(
        self, s: AsyncSession, group_id: int, user_id: str
    ):
        group = (
            await s.execute(
                select(GroupChat).where(GroupChat.id == group_id).with_for_update()
            )
        ).scalar_one_or_none()
        if group is None or group.dissolved_at is not None:
            return None
        if await is_member(s, group_id, user_id) is None:
            return None
        return group

    async def update_group(
        self,
        group_id: int,
        user_id: str,
        name: Optional[str] = None,
        history_visible: Optional[bool] = None,
        session: AsyncSession | None = None,
    ) -> dict:
        async with using_session(session) as s:
            group = await self._locked_group_for_member(s, group_id, user_id)
            if group is None:
                return {"success": False, "error": "not_found"}
            if group.owner_id != user_id:
                return {"success": False, "error": "not_owner"}
            system_messages = []
            if name is not None:
                cleaned = _clean_name(name)
                if cleaned is None:
                    return {"success": False, "error": "invalid_name"}
                if cleaned != group.name:
                    group.name = cleaned
                    await s.flush()
                    system_messages.append(
                        await add_system_message(s, group_id, f"renamed:{cleaned}")
                    )
            if (
                history_visible is not None
                and bool(history_visible) != group.history_visible
            ):
                group.history_visible = bool(history_visible)
                await s.flush()
                state = "on" if history_visible else "off"
                system_messages.append(
                    await add_system_message(s, group_id, f"history_visible:{state}")
                )
            return {
                "success": True,
                "group": _group_summary(group, await _member_count(s, group_id)),
                "system_messages": system_messages,
            }

    async def leave_group(
        self, group_id: int, user_id: str, session: AsyncSession | None = None
    ) -> dict:
        async with using_session(session) as s:
            group = await self._locked_group_for_member(s, group_id, user_id)
            if group is None:
                return {"success": False, "error": "not_found"}
            await s.execute(
                delete(GroupMember).where(
                    GroupMember.group_id == group_id, GroupMember.user_id == user_id
                )
            )
            # 退群後這個群的 AI 助理問答也不留（同一交易）
            await chat_assistant_history_repo.invalidate_chat(
                "group", group_id, user_id=user_id, session=s
            )
            remaining = await _member_ids(s, group_id)
            if not remaining:
                # 刪群會連帶刪掉邀請（CASCADE）；先記下還沒處理的，router 要清掉對方的邀請通知
                pending = (
                    await s.execute(
                        select(GroupInvite.id, GroupInvite.invitee_id).where(
                            GroupInvite.group_id == group_id,
                            GroupInvite.status == "pending",
                        )
                    )
                ).all()
                await s.execute(delete(GroupChat).where(GroupChat.id == group_id))
                return {
                    "success": True,
                    "deleted": True,
                    "new_owner_id": None,
                    "system_messages": [],
                    "member_ids": [],
                    "cancelled_invites": [
                        {"invite_id": invite_id, "invitee_id": invitee_id}
                        for invite_id, invitee_id in pending
                    ],
                }
            system_messages = [
                await add_system_message(s, group_id, f"member_left:{user_id}")
            ]
            new_owner_id = None
            if group.owner_id == user_id:
                new_owner_id = remaining[0]  # 最早入群的人
                group.owner_id = new_owner_id
                await s.flush()
                system_messages.append(
                    await add_system_message(
                        s, group_id, f"owner_changed:{new_owner_id}"
                    )
                )
            return {
                "success": True,
                "deleted": False,
                "new_owner_id": new_owner_id,
                "system_messages": system_messages,
                "member_ids": remaining,
            }

    async def remove_member(
        self,
        group_id: int,
        owner_id: str,
        target_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        async with using_session(session) as s:
            group = await self._locked_group_for_member(s, group_id, owner_id)
            if group is None:
                return {"success": False, "error": "not_found"}
            if group.owner_id != owner_id:
                return {"success": False, "error": "not_owner"}
            if target_id == owner_id:
                return {"success": False, "error": "cannot_remove_self"}
            deleted = await s.execute(
                delete(GroupMember).where(
                    GroupMember.group_id == group_id, GroupMember.user_id == target_id
                )
            )
            if deleted.rowcount == 0:
                return {"success": False, "error": "target_not_member"}
            await chat_assistant_history_repo.invalidate_chat(
                "group", group_id, user_id=target_id, session=s
            )
            system_message = await add_system_message(
                s, group_id, f"member_removed:{target_id}"
            )
            return {
                "success": True,
                "group_name": group.name,  # 被踢的人收到的通知要寫群名
                "system_message": system_message,
                "member_ids": await _member_ids(s, group_id),
            }

    async def transfer_owner(
        self,
        group_id: int,
        owner_id: str,
        target_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        """群主手動交棒：對方要是成員而且是 Pro（Pro 到期的人唯讀，接了也什麼都不能管）"""
        async with using_session(session) as s:
            group = await self._locked_group_for_member(s, group_id, owner_id)
            if group is None:
                return {"success": False, "error": "not_found"}
            if group.owner_id != owner_id:
                return {"success": False, "error": "not_owner"}
            if target_id == owner_id:
                return {"success": False, "error": "cannot_transfer_to_self"}
            if await is_member(s, group_id, target_id) is None:
                return {"success": False, "error": "target_not_member"}
            is_premium = (
                await s.execute(
                    select(User.user_id).where(
                        User.user_id == target_id, premium_clause()
                    )
                )
            ).scalar_one_or_none()
            if is_premium is None:
                return {"success": False, "error": "target_not_premium"}
            group.owner_id = target_id
            await s.flush()
            system_message = await add_system_message(
                s, group_id, f"owner_changed:{target_id}"
            )
            return {
                "success": True,
                "system_message": system_message,
                "member_ids": await _member_ids(s, group_id),
            }

    async def dissolve_group(
        self, group_id: int, owner_id: str, session: AsyncSession | None = None
    ) -> dict:
        """群主解散（c068）：成員全部移出、待處理邀請取消、留一則系統訊息，訊息本身不刪（檢舉證據）。
        owner_id 設 NULL：解散的群不佔群主的開群上限。回傳原本的成員（router 推播＋通知用）。"""
        async with using_session(session) as s:
            group = await self._locked_group_for_member(s, group_id, owner_id)
            if group is None:
                return {"success": False, "error": "not_found"}
            if group.owner_id != owner_id:
                return {"success": False, "error": "not_owner"}
            member_ids = await _member_ids(s, group_id)
            now = datetime.now(timezone.utc)
            # router 要清掉被邀的人鈴鐺裡的邀請通知（同最後一人退群刪群）
            cancelled = (
                await s.execute(
                    update(GroupInvite)
                    .where(
                        GroupInvite.group_id == group_id,
                        GroupInvite.status == "pending",
                    )
                    .values(status="cancelled", responded_at=now)
                    .returning(GroupInvite.id, GroupInvite.invitee_id)
                )
            ).all()
            system_message = await add_system_message(
                s, group_id, f"dissolved:{owner_id}"
            )
            await s.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
            # 所有成員的 AI 助理問答一起清掉（群已經不存在於他們的畫面）
            await chat_assistant_history_repo.invalidate_chat(
                "group", group_id, session=s
            )
            group.dissolved_at = now
            group.owner_id = None
            await s.flush()
            return {
                "success": True,
                "group_name": group.name,
                "member_ids": member_ids,
                "system_message": system_message,
                "cancelled_invites": [
                    {"invite_id": invite_id, "invitee_id": invitee_id}
                    for invite_id, invitee_id in cancelled
                ],
            }

    async def set_muted(
        self,
        group_id: int,
        user_id: str,
        muted: bool,
        session: AsyncSession | None = None,
    ) -> dict:
        async with using_session(session) as s:
            result = await s.execute(
                update(GroupMember)
                .where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
                .values(muted=bool(muted))
            )
            if result.rowcount == 0:
                return {"success": False, "error": "not_found"}
            return {"success": True}

    # ── 邀請 ──────────────────────────────────────────────

    async def create_invites(
        self,
        group_id: int,
        inviter_id: str,
        invitee_ids: Iterable[str],
        session: AsyncSession | None = None,
    ) -> dict:
        wanted = list(dict.fromkeys(i for i in invitee_ids if i))[
            :MAX_INVITEES_PER_REQUEST
        ]
        async with using_session(session) as s:
            group = await self._locked_group_for_member(s, group_id, inviter_id)
            if group is None:
                return {"success": False, "error": "not_found"}
            if await _member_count(s, group_id) >= await _limit(
                s, "limit_group_members"
            ):
                return {"success": False, "error": "group_full"}
            since = datetime.now(timezone.utc) - timedelta(days=1)
            sent_today = (
                await s.execute(
                    select(func.count())
                    .select_from(GroupInvite)
                    .where(
                        GroupInvite.inviter_id == inviter_id,
                        GroupInvite.created_at > since,
                    )
                )
            ).scalar_one()
            remaining_quota = await _limit(s, "limit_daily_group_invites") - sent_today
            if remaining_quota <= 0:
                return {"success": False, "error": "daily_invite_limit_reached"}
            join_limit = await _limit(s, "limit_group_join")

            invited, skipped = [], []
            for invitee_id in wanted:
                reason = await self._invite_skip_reason(
                    s, group_id, inviter_id, invitee_id, join_limit
                )
                if reason is None and len(invited) >= remaining_quota:
                    reason = "daily_invite_limit_reached"
                if reason:
                    skipped.append({"user_id": invitee_id, "reason": reason})
                    continue
                invite = GroupInvite(
                    group_id=group_id, inviter_id=inviter_id, invitee_id=invitee_id
                )
                s.add(invite)
                await s.flush()
                invited.append({"invite_id": invite.id, "invitee_id": invitee_id})
            return {
                "success": True,
                "group_name": group.name,
                "invited": invited,
                "skipped": skipped,
            }

    async def _invite_skip_reason(
        self,
        s: AsyncSession,
        group_id: int,
        inviter_id: str,
        invitee_id: str,
        join_limit: int,
    ) -> Optional[str]:
        if invitee_id == inviter_id:
            return "self"
        statuses = {
            row[0]
            for row in await s.execute(
                select(Friendship.status).where(_pair(inviter_id, invitee_id))
            )
        }
        if "blocked" in statuses:
            return "blocked"
        if "accepted" not in statuses:
            return "not_friend"
        premium = (
            await s.execute(
                select(User.user_id).where(User.user_id == invitee_id, premium_clause())
            )
        ).first()
        if premium is None:
            return "not_premium"
        if await is_member(s, group_id, invitee_id) is not None:
            return "already_member"
        pending = (
            await s.execute(
                select(GroupInvite.id).where(
                    GroupInvite.group_id == group_id,
                    GroupInvite.invitee_id == invitee_id,
                    GroupInvite.status == "pending",
                )
            )
        ).first()
        if pending is not None:
            return "already_invited"
        if await _joined_count(s, invitee_id) >= join_limit:
            return "join_limit_reached"
        return None

    async def list_invites(
        self, user_id: str, session: AsyncSession | None = None
    ) -> List[dict]:
        count_sq = (
            select(func.count())
            .select_from(GroupMember)
            .where(GroupMember.group_id == GroupChat.id)
            .correlate(GroupChat)
            .scalar_subquery()
        )
        stmt = (
            select(
                GroupInvite, GroupChat.name, count_sq, User.username, User.display_name
            )
            .join(GroupChat, GroupChat.id == GroupInvite.group_id)
            .join(User, User.user_id == GroupInvite.inviter_id)
            .where(
                GroupInvite.invitee_id == user_id,
                GroupInvite.status == "pending",
                GroupChat.dissolved_at.is_(None),  # 解散時邀請已取消；這裡是保險
            )
            .order_by(GroupInvite.created_at.desc())
        )
        async with using_session(session) as s:
            rows = (await s.execute(stmt)).all()
        return [
            {
                "invite_id": inv.id,
                "group_id": inv.group_id,
                "group_name": name,
                "member_count": member_count,
                "inviter_id": inv.inviter_id,
                "inviter_name": display_name or username or inv.inviter_id,
                "created_at": _iso(inv.created_at),
            }
            for inv, name, member_count, username, display_name in rows
        ]

    async def accept_invite(
        self, invite_id: int, user_id: str, session: AsyncSession | None = None
    ) -> dict:
        pending = (
            GroupInvite.id == invite_id,
            GroupInvite.invitee_id == user_id,
            GroupInvite.status == "pending",
        )
        async with using_session(session) as s:
            group_id = (
                await s.execute(select(GroupInvite.group_id).where(*pending))
            ).scalar_one_or_none()
            if group_id is None:
                return {"success": False, "error": "invite_not_found"}
            # 先鎖群組列（同群的接受排隊，人數上限不會被兩個人同時擠破）再鎖邀請列：
            # 跟解散／最後一人退群同一個順序，反過來會互相卡死
            group = (
                await s.execute(
                    select(GroupChat).where(GroupChat.id == group_id).with_for_update()
                )
            ).scalar_one_or_none()
            if group is None or group.dissolved_at is not None:
                return {"success": False, "error": "invite_not_found"}
            # 等鎖的期間邀請可能已被取消／處理掉：鎖到之後再確認一次
            invite = (
                await s.execute(select(GroupInvite).where(*pending).with_for_update())
            ).scalar_one_or_none()
            if invite is None:
                return {"success": False, "error": "invite_not_found"}
            await _lock_user(s, user_id)
            premium = (
                await s.execute(
                    select(User.user_id).where(
                        User.user_id == user_id, premium_clause()
                    )
                )
            ).first()
            if premium is None:
                return {"success": False, "error": "not_premium"}
            if await _member_count(s, group.id) >= await _limit(
                s, "limit_group_members"
            ):
                return {"success": False, "error": "group_full"}
            if await _joined_count(s, user_id) >= await _limit(s, "limit_group_join"):
                return {"success": False, "error": "join_limit_reached"}

            if group.history_visible:
                first_visible = 0
            else:
                first_visible = (
                    await s.execute(
                        select(func.coalesce(func.max(GroupMessage.id), 0)).where(
                            GroupMessage.group_id == group.id
                        )
                    )
                ).scalar_one()
            s.add(
                GroupMember(
                    group_id=group.id,
                    user_id=user_id,
                    first_visible_message_id=first_visible,
                )
            )
            invite.status = "accepted"
            invite.responded_at = datetime.now(timezone.utc)
            await s.flush()
            system_message = await add_system_message(
                s, group.id, f"member_joined:{user_id}"
            )
            return {
                "success": True,
                "group_id": group.id,
                "group_name": group.name,
                "inviter_id": invite.inviter_id,
                "system_message": system_message,
                "member_ids": await _member_ids(s, group.id),
            }

    async def decline_invite(
        self, invite_id: int, user_id: str, session: AsyncSession | None = None
    ) -> dict:
        async with using_session(session) as s:
            row = (
                await s.execute(
                    update(GroupInvite)
                    .where(
                        GroupInvite.id == invite_id,
                        GroupInvite.invitee_id == user_id,
                        GroupInvite.status == "pending",
                    )
                    .values(status="declined", responded_at=datetime.now(timezone.utc))
                    .returning(GroupInvite.group_id, GroupInvite.inviter_id)
                )
            ).first()
            if row is None:
                return {"success": False, "error": "invite_not_found"}
            return {"success": True, "group_id": row[0], "inviter_id": row[1]}


group_chat_repo = GroupChatRepository()
