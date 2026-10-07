"""
Async ORM repository for Notification operations.

Provides async equivalents of the functions in core.database.notifications,
using SQLAlchemy 2.0 select/update with the Notification model.

Usage::

    from core.orm.notifications_repo import notifications_repo

    notification = await notifications_repo.create_notification("user-1", "system", "Title", "Body")
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Notification
from .session import using_session

logger = logging.getLogger(__name__)

RECALLED_MESSAGE_BODY = "訊息已收回"

# 論壇互動通知（notify_post_activity）：後端存中文，前端 notification-service.describe 依 interaction_type 走 i18n
POST_ACTIVITY_TITLE = "論壇"
POST_ACTIVITY_PEOPLE_MAX = (
    100  # 記下的不同人數上限（夠算「等 N 人」，不讓 JSON 無限長）
)
_POST_ACTIVITY_TEXT = {
    "push": (
        "{name} 推了你的文章「{title}」",
        "{name} 等 {count} 人推了你的文章「{title}」",
    ),
    "comment": (
        "{name} 留言了你的文章「{title}」",
        "{name} 等 {count} 人留言了你的文章「{title}」",
    ),
    "thread_reply": (
        "{name} 也在「{title}」留言",
        "{name} 等 {count} 人也在「{title}」留言",
    ),
}


def _post_activity_body(kind: str, name: str, count: int, title: str) -> str:
    one, many = _POST_ACTIVITY_TEXT[kind]
    return (many if count > 1 else one).format(name=name, count=count, title=title)


def _message_preview(content: str) -> str:
    """私訊通知的內容預覽：前 50 字，超過加 ...（收回時要用同一個算法比對舊通知）"""
    preview = content[:50]
    return preview + "..." if len(content) > 50 else preview


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "type": row.type,
        "title": row.title,
        "body": row.body,
        "data": row.data,
        "is_read": row.is_read,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class NotificationsRepository:
    # ── Core CRUD ────────────────────────────────────────────────────────────

    async def create_notification(
        self,
        user_id: str,
        notification_type: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        session: AsyncSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        notification_id = f"notif_{uuid.uuid4().hex[:12]}"
        notification = Notification(
            id=notification_id,
            user_id=user_id,
            type=notification_type,
            title=title,
            body=body,
            data=data,
            is_read=False,
        )

        async with using_session(session) as s:
            s.add(notification)
            await s.flush()
            await s.refresh(notification)
            return _row_to_dict(notification)

    async def get_notifications(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        unread_only: bool = False,
        session: AsyncSession | None = None,
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def get_unread_count(
        self,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            return result.scalar_one() or 0

    async def mark_notification_as_read(
        self,
        notification_id: str,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> bool:
        stmt = (
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            .values(is_read=True)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            return result.rowcount > 0

    async def mark_all_as_read(
        self,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> int:
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            return result.rowcount

    async def delete_notification(
        self,
        notification_id: str,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> bool:
        stmt = delete(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            return result.rowcount > 0

    # ── Helper: specific notification types ──────────────────────────────────

    async def notify_new_message(
        self,
        to_user_id: str,
        from_user_id: str,
        from_username: str,
        message_preview: str,
        conversation_id: str,
        message_id: Optional[int] = None,
        session: AsyncSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """私訊通知：同一對話還沒讀的合併成一筆（最新內容＋則數），像 LINE／Teams。
        message_id 記下預覽顯示的是哪一則——收回時靠它把預覽改掉。"""
        body = f"{from_username}: {_message_preview(message_preview)}"
        conv_key = str(conversation_id)

        async with using_session(session) as s:
            # 同一（收件人, 對話）的通知寫入排隊：兩則幾乎同時到、還沒有未讀那筆時，
            # FOR UPDATE 鎖不到不存在的列，會各插一筆。交易結束自動釋放。
            await s.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtext(f"notif-dm:{to_user_id}:{conv_key}")
                    )
                )
            )
            existing = (
                await s.execute(
                    select(Notification)
                    .where(
                        Notification.user_id == to_user_id,
                        Notification.type == "message",
                        Notification.is_read.is_(False),
                        Notification.data["conversation_id"].astext == conv_key,
                    )
                    .order_by(Notification.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if existing is not None:
                data = dict(existing.data or {})
                data["from_user_id"] = from_user_id
                data["from_username"] = from_username
                data["count"] = int(data.get("count") or 1) + 1
                if message_id is not None:
                    data["message_id"] = str(message_id)
                else:
                    data.pop("message_id", None)  # 不然會指到上一則
                existing.body = body
                existing.data = data
                existing.created_at = func.now()  # 浮到最上面
                await s.flush()
                await s.refresh(existing)
                return _row_to_dict(existing)

            return await self.create_notification(
                user_id=to_user_id,
                notification_type="message",
                title="新訊息",
                body=body,
                data={
                    "from_user_id": from_user_id,
                    "from_username": from_username,
                    "conversation_id": conv_key,
                    "count": 1,
                    **(
                        {"message_id": str(message_id)}
                        if message_id is not None
                        else {}
                    ),
                },
                session=s,
            )

    async def mark_message_recalled(
        self,
        to_user_id: str,
        conversation_id: int,
        message_id: int,
        original_content: str,
        session: AsyncSession | None = None,
    ) -> List[Dict[str, Any]]:
        """私訊被收回：預覽顯示那一則的通知改成「訊息已收回」，回傳改過的（給 WS 推）。

        新通知靠 data.message_id 精準比對；之前的舊通知沒有 message_id，退而比對整段
        body（＝「名字: 預覽」）——只比結尾會誤中：收回 "test" 時把 "prefix: test" 的通知
        也改掉。合併通知若已經顯示後面的訊息就不動。
        """
        msg_key = Notification.data["message_id"].astext
        legacy_body = func.concat(
            func.coalesce(Notification.data["from_username"].astext, ""),
            f": {_message_preview(original_content)}",
        )
        stmt = select(Notification).where(
            Notification.user_id == to_user_id,
            Notification.type == "message",
            Notification.data["conversation_id"].astext == str(conversation_id),
            or_(
                msg_key == str(message_id),
                and_(msg_key.is_(None), Notification.body == legacy_body),
            ),
        )
        async with using_session(session) as s:
            rows = (await s.execute(stmt)).scalars().all()
            for n in rows:
                data = dict(n.data or {})
                data["recalled"] = True
                n.data = data
                name = data.get("from_username") or data.get("from_user_id") or ""
                n.body = f"{name}: {RECALLED_MESSAGE_BODY}"
            await s.flush()
            return [_row_to_dict(n) for n in rows]

    async def resolve_friend_request_notifications(
        self,
        user_id: str,
        from_user_id: str,
        session: AsyncSession | None = None,
    ) -> List[str]:
        """from_user_id 對 user_id 的好友邀請處理掉了（接受／拒絕／對方取消／重送）→
        那筆變已讀，鈴鐺不再掛著按了只會 400 的接受／拒絕鈕。回傳被清掉的 id。"""
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.type == "friend_request",
                Notification.is_read.is_(False),
                Notification.data["from_user_id"].astext == from_user_id,
            )
            .values(is_read=True)
            .returning(Notification.id)
        )
        async with using_session(session) as s:
            result = await s.execute(stmt)
            return [row[0] for row in result.fetchall()]

    async def mark_message_notifications_read(
        self,
        user_id: str,
        conversation_id: int | str,
        session: AsyncSession | None = None,
    ) -> List[str]:
        """讀了對話 → 該對話的私訊通知一起變已讀，回傳被清掉的 id（推給其他分頁／裝置）。"""
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.type == "message",
                Notification.is_read.is_(False),
                Notification.data["conversation_id"].astext == str(conversation_id),
            )
            .values(is_read=True)
            .returning(Notification.id)
        )
        async with using_session(session) as s:
            result = await s.execute(stmt)
            return [row[0] for row in result.fetchall()]

    # ── 群組聊天（c066）─────────────────────────────────────────────────────

    async def notify_group_message(
        self,
        to_user_id: str,
        group_id: int,
        group_name: str,
        from_user_id: str,
        from_name: str,
        message_preview: str,
        message_id: int,
        mentioned: bool = False,
        session: AsyncSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """群組新訊息：同一群還沒讀的合併成一筆（最新內容＋則數），同私訊的 notify_new_message。
        mentioned：這則有 @ 到收件人 → data.mentioned=True，之後合併進來的一般訊息不會蓋掉（列表的「有人提及你」看這個）"""
        body = f"{from_name}: {_message_preview(message_preview)}"
        group_key = str(group_id)
        async with using_session(session) as s:
            await s.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtext(f"notif-group:{to_user_id}:{group_key}")
                    )
                )
            )
            existing = (
                await s.execute(
                    select(Notification)
                    .where(
                        Notification.user_id == to_user_id,
                        Notification.type == "group_message",
                        Notification.is_read.is_(False),
                        Notification.data["group_id"].astext == group_key,
                    )
                    .order_by(Notification.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                data = dict(existing.data or {})
                data.update(
                    from_user_id=from_user_id,
                    from_username=from_name,
                    group_name=group_name,
                    message_id=str(message_id),
                    count=int(data.get("count") or 1) + 1,
                )
                if mentioned:
                    data["mentioned"] = True
                existing.title = group_name
                existing.body = body
                existing.data = data
                existing.created_at = func.now()
                await s.flush()
                await s.refresh(existing)
                return _row_to_dict(existing)
            return await self.create_notification(
                user_id=to_user_id,
                notification_type="group_message",
                title=group_name,
                body=body,
                data={
                    "group_id": group_key,
                    "group_name": group_name,
                    "from_user_id": from_user_id,
                    "from_username": from_name,
                    "message_id": str(message_id),
                    "count": 1,
                    **({"mentioned": True} if mentioned else {}),
                },
                session=s,
            )

    async def unread_mention_group_ids(
        self, user_id: str, session: AsyncSession | None = None
    ) -> set:
        """有人 @ 我、我還沒讀的群組 id（讀了群組 mark_group_message_notifications_read 就清掉）"""
        stmt = select(Notification.data["group_id"].astext).where(
            Notification.user_id == user_id,
            Notification.type == "group_message",
            Notification.is_read.is_(False),
            Notification.data["mentioned"].astext == "true",
        )
        async with using_session(session) as s:
            rows = await s.execute(stmt)
            return {int(r[0]) for r in rows if r[0] and r[0].isdigit()}

    async def mark_group_message_notifications_read(
        self,
        user_id: str,
        group_id: int | str,
        session: AsyncSession | None = None,
    ) -> List[str]:
        """讀了群組 → 該群的訊息通知一起變已讀，回傳被清掉的 id"""
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.type == "group_message",
                Notification.is_read.is_(False),
                Notification.data["group_id"].astext == str(group_id),
            )
            .values(is_read=True)
            .returning(Notification.id)
        )
        async with using_session(session) as s:
            result = await s.execute(stmt)
            return [row[0] for row in result.fetchall()]

    async def resolve_group_invite_notifications(
        self,
        user_id: str,
        invite_id: int,
        session: AsyncSession | None = None,
    ) -> List[str]:
        """邀請處理掉了（接受／拒絕）→ 那筆變已讀，鈴鐺不再掛著按了只會失敗的按鈕"""
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.type == "group_invite",
                Notification.is_read.is_(False),
                Notification.data["invite_id"].astext == str(invite_id),
            )
            .values(is_read=True)
            .returning(Notification.id)
        )
        async with using_session(session) as s:
            result = await s.execute(stmt)
            return [row[0] for row in result.fetchall()]

    async def mark_group_message_recalled(
        self,
        group_id: int,
        message_id: int,
        session: AsyncSession | None = None,
    ) -> List[Dict[str, Any]]:
        """群組訊息被收回：還在顯示那則預覽的通知改成「訊息已收回」（原文不能留在通知裡）"""
        async with using_session(session) as s:
            rows = (
                (
                    await s.execute(
                        select(Notification).where(
                            Notification.type == "group_message",
                            Notification.data["group_id"].astext == str(group_id),
                            Notification.data["message_id"].astext == str(message_id),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for n in rows:
                data = dict(n.data or {})
                data["recalled"] = True
                n.data = data
                name = data.get("from_username") or data.get("from_user_id") or ""
                n.body = f"{name}: {RECALLED_MESSAGE_BODY}"
            await s.flush()
            return [_row_to_dict(n) for n in rows]

    # ── 論壇（2026-10-02）─────────────────────────────────────────────────

    async def notify_post_activity(
        self,
        to_user_id: str,
        kind: str,
        post_id: int,
        post_title: str,
        from_user_id: str,
        from_name: str,
        session: AsyncSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """論壇互動通知，同一篇同一種還沒讀的合併成一筆（「小明等 3 人推了你的文章」）。
        kind：push（推你的文章）、comment（留言你的文章）、thread_reply（你留言過的文章有新留言）。
        count＝不同的人數：同一個人取消再推、連留好幾則都只算一次。噓不發通知（router 決定）。"""
        title_display = post_title[:30] + ("..." if len(post_title) > 30 else "")
        key = (str(post_id), kind)
        async with using_session(session) as s:
            await s.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtext(f"notif-post:{to_user_id}:{key[0]}:{kind}")
                    )
                )
            )
            existing = (
                await s.execute(
                    select(Notification)
                    .where(
                        Notification.user_id == to_user_id,
                        Notification.type == "post_interaction",
                        Notification.is_read.is_(False),
                        Notification.data["post_id"].astext == key[0],
                        Notification.data["interaction_type"].astext == kind,
                    )
                    .order_by(Notification.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            data = dict(existing.data or {}) if existing is not None else {}
            people = [p for p in (data.get("from_user_ids") or []) if p != from_user_id]
            people = [from_user_id, *people][:POST_ACTIVITY_PEOPLE_MAX]
            data.update(
                post_id=post_id,
                post_title=title_display,
                interaction_type=kind,
                from_user_id=from_user_id,
                from_username=from_name,
                from_user_ids=people,
                count=len(people),
            )
            body = _post_activity_body(kind, from_name, len(people), title_display)
            if existing is not None:
                existing.title = POST_ACTIVITY_TITLE
                existing.body = body
                existing.data = data
                existing.created_at = func.now()
                await s.flush()
                await s.refresh(existing)
                return _row_to_dict(existing)
            return await self.create_notification(
                user_id=to_user_id,
                notification_type="post_interaction",
                title=POST_ACTIVITY_TITLE,
                body=body,
                data=data,
                session=s,
            )

    async def has_unread_forum_activity(
        self, user_id: str, session: AsyncSession | None = None
    ) -> bool:
        """功能選單「論壇」紅點：有未讀的留言類通知（留言你的文章／也在你留言過的文章留言）；推不算"""
        stmt = select(
            select(Notification.id)
            .where(
                Notification.user_id == user_id,
                Notification.type == "post_interaction",
                Notification.is_read.is_(False),
                Notification.data["interaction_type"].astext.in_(
                    ("comment", "thread_reply")
                ),
            )
            .exists()
        )
        async with using_session(session) as s:
            return bool((await s.execute(stmt)).scalar())

    async def mark_post_notifications_read(
        self,
        user_id: str,
        post_id: int,
        session: AsyncSession | None = None,
    ) -> List[str]:
        """打開文章 → 這篇的論壇通知一起變已讀（同讀了群組清群組通知），回傳被清掉的 id"""
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.type == "post_interaction",
                Notification.is_read.is_(False),
                Notification.data["post_id"].astext == str(post_id),
            )
            .values(is_read=True)
            .returning(Notification.id)
        )
        async with using_session(session) as s:
            result = await s.execute(stmt)
            return [row[0] for row in result.fetchall()]

    async def resolve_post_notifications(
        self,
        post_id: int,
        session: AsyncSession | None = None,
    ) -> Dict[str, List[str]]:
        """文章被作者刪除 → 這篇的論壇通知對所有人變已讀：按進去只會 404，
        未讀還會把論壇紅點永遠釘住。回傳 {user_id: [被清掉的 id]}，給 router 推到各人開著的鈴鐺。
        （被管理員隱藏的文章沒走這條，由 get_post_detail 的 404 分支在使用者點進去時清自己的。）"""
        stmt = (
            update(Notification)
            .where(
                Notification.type == "post_interaction",
                Notification.is_read.is_(False),
                Notification.data["post_id"].astext == str(post_id),
            )
            .values(is_read=True)
            .returning(Notification.user_id, Notification.id)
        )
        async with using_session(session) as s:
            result = await s.execute(stmt)
            cleared: Dict[str, List[str]] = {}
            for user_id, notification_id in result.fetchall():
                cleared.setdefault(user_id, []).append(notification_id)
            return cleared


notifications_repo = NotificationsRepository()
