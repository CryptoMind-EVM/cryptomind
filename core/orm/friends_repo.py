"""
Async ORM repository for Friendship operations.

Provides async equivalents of the functions in core.database.friends,
using SQLAlchemy 2.0 select/update with Friendship and User models.

Usage::

    from core.orm.friends_repo import friends_repo

    result = await friends_repo.send_friend_request("user-1", "user-2")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import case, delete, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Friendship, Post, User
from .session import using_session

logger = logging.getLogger(__name__)


def _fmt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _pair(a: str, b: str):
    """a、b 之間那一列（不分方向）"""
    return or_(
        (Friendship.user_id == a) & (Friendship.friend_id == b),
        (Friendship.user_id == b) & (Friendship.friend_id == a),
    )


async def _lock_pair(s: AsyncSession, a: str, b: str) -> None:
    """同一對人的好友／封鎖寫入排隊（交易結束自動釋放）。

    一對人只能有一列（idx_friendships_ordered_pair），兩人之間還沒有列時
    SELECT ... FOR UPDATE 鎖不到東西：同時互相封鎖／互加好友會兩邊都 INSERT，
    後到的撞唯一索引 → 500。
    """
    lo, hi = sorted((a, b))
    await s.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"friendship:{lo}:{hi}")))
    )


def _blocked_by(
    row_user_id: str, row_status: str, row_mutual: bool, viewer: str
) -> bool:
    """這列表示 viewer 有封鎖對方嗎（先封鎖的人，或 mutual 的另一方）"""
    return row_status == "blocked" and (row_user_id == viewer or bool(row_mutual))


class FriendsRepository:
    async def search_users(
        self,
        query: str,
        limit: int = 20,
        exclude_user_id: Optional[str] = None,
        session: AsyncSession | None = None,
    ) -> List[dict]:
        pattern = f"%{query.replace('%', r'\%').replace('_', r'\_')}%"
        stmt = (
            select(
                User.user_id,
                User.username,
                User.membership_tier,
                User.created_at,
                User.display_name,
            )
            .where(
                # 帳號名（EVM_xxxx）或暱稱都搜得到：改了暱稱的人，朋友只記得暱稱
                or_(
                    User.username.ilike(pattern, escape="\\"),
                    User.display_name.ilike(pattern, escape="\\"),
                )
            )
            .order_by(User.username.asc())
            .limit(limit)
        )
        if exclude_user_id:
            stmt = stmt.where(User.user_id != exclude_user_id)

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.fetchall()
            return [
                {
                    "user_id": r[0],
                    "username": r[1],
                    "display_name": r[4],
                    "membership_tier": r[2] or "free",
                    "member_since": _fmt(r[3]),
                }
                for r in rows
            ]

    async def get_friends_list(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        session: AsyncSession | None = None,
    ) -> List[dict]:
        friend_alias = User
        stmt = (
            select(
                friend_alias.user_id,
                friend_alias.username,
                friend_alias.membership_tier,
                Friendship.updated_at,
                friend_alias.last_active_at,
                friend_alias.display_name,
            )
            .join(
                friend_alias,
                or_(
                    (Friendship.user_id == user_id)
                    & (Friendship.friend_id == friend_alias.user_id),
                    (Friendship.friend_id == user_id)
                    & (Friendship.user_id == friend_alias.user_id),
                ),
            )
            .where(
                or_(
                    Friendship.user_id == user_id,
                    Friendship.friend_id == user_id,
                ),
                Friendship.status == "accepted",
                friend_alias.user_id != user_id,
            )
            .order_by(Friendship.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.fetchall()
            return [
                {
                    "user_id": r[0],
                    "username": r[1],
                    "membership_tier": r[2] or "free",
                    "friends_since": _fmt(r[3]),
                    "last_active_at": _fmt(r[4]),
                    "display_name": r[5],
                }
                for r in rows
            ]

    async def get_friendship_status(
        self,
        user_id: str,
        other_user_id: str,
        session: AsyncSession | None = None,
    ) -> Optional[dict]:
        stmt = select(Friendship).where(
            or_(
                (Friendship.user_id == user_id)
                & (Friendship.friend_id == other_user_id),
                (Friendship.user_id == other_user_id)
                & (Friendship.friend_id == user_id),
            )
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return {
                "id": row.id,
                "requester_id": row.user_id,
                "target_id": row.friend_id,
                "status": row.status,
                "created_at": _fmt(row.created_at),
                "updated_at": _fmt(row.updated_at),
                "is_requester": row.user_id == user_id,
                # 前端只對「我封鎖的人」給解除按鈕（被對方封鎖時按了只會 400）
                "blocked_by_me": _blocked_by(
                    row.user_id, row.status, row.mutual_block, user_id
                ),
            }

    async def send_friend_request(
        self,
        from_user_id: str,
        to_user_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        if from_user_id == to_user_id:
            return {"success": False, "error": "cannot_add_self"}

        async with using_session(session) as s:
            await _lock_pair(s, from_user_id, to_user_id)
            stmt = (
                select(Friendship)
                .where(
                    or_(
                        (Friendship.user_id == from_user_id)
                        & (Friendship.friend_id == to_user_id),
                        (Friendship.user_id == to_user_id)
                        & (Friendship.friend_id == from_user_id),
                    )
                )
                .with_for_update()
            )
            result = await s.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                if existing.status == "accepted":
                    return {"success": False, "error": "already_friends"}
                if existing.status == "pending":
                    if existing.user_id == to_user_id:
                        await s.execute(
                            update(Friendship)
                            .where(
                                Friendship.user_id == to_user_id,
                                Friendship.friend_id == from_user_id,
                            )
                            .values(
                                status="accepted",
                                updated_at=datetime.now(timezone.utc),
                            )
                        )
                        return {
                            "success": True,
                            "message": "friend_added",
                            "auto_accepted": True,
                        }
                    return {"success": False, "error": "request_pending"}
                if existing.status == "blocked":
                    # 我也在封鎖（我先封鎖，或互相封鎖）→ 先請我自己解除
                    if _blocked_by(
                        existing.user_id,
                        existing.status,
                        existing.mutual_block,
                        from_user_id,
                    ):
                        return {"success": False, "error": "you_blocked_user"}
                    return {"success": False, "error": "user_blocked_you"}
                if existing.status == "rejected":
                    # 被拒絕的紀錄可能是反方向（對方曾邀請我、我拒絕了）：
                    # 用 id 更新並改成「我 → 對方」，對方才會收到這次的邀請
                    await s.execute(
                        update(Friendship)
                        .where(Friendship.id == existing.id)
                        .values(
                            user_id=from_user_id,
                            friend_id=to_user_id,
                            status="pending",
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    return {
                        "success": True,
                        "message": "request_resent",
                        "request_id": existing.id,
                    }

            now = datetime.now(timezone.utc)
            new_friendship = Friendship(
                user_id=from_user_id,
                friend_id=to_user_id,
                status="pending",
                created_at=now,
                updated_at=now,
            )
            s.add(new_friendship)
            await s.flush()
            return {
                "success": True,
                "message": "request_sent",
                "request_id": new_friendship.id,
            }

    async def accept_friend_request(
        self,
        user_id: str,
        requester_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        stmt = (
            update(Friendship)
            .where(
                Friendship.user_id == requester_id,
                Friendship.friend_id == user_id,
                Friendship.status == "pending",
            )
            .values(status="accepted", updated_at=datetime.now(timezone.utc))
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            if result.rowcount == 0:
                return {"success": False, "error": "request_not_found"}
            return {"success": True, "message": "friend_added"}

    async def reject_friend_request(
        self,
        user_id: str,
        requester_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        stmt = (
            update(Friendship)
            .where(
                Friendship.user_id == requester_id,
                Friendship.friend_id == user_id,
                Friendship.status == "pending",
            )
            .values(status="rejected", updated_at=datetime.now(timezone.utc))
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            if result.rowcount == 0:
                return {"success": False, "error": "request_not_found"}
            return {"success": True, "message": "request_rejected"}

    async def remove_friend(
        self,
        user_id: str,
        friend_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        stmt = delete(Friendship).where(
            or_(
                (Friendship.user_id == user_id) & (Friendship.friend_id == friend_id),
                (Friendship.user_id == friend_id) & (Friendship.friend_id == user_id),
            ),
            Friendship.status == "accepted",
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            if result.rowcount == 0:
                return {"success": False, "error": "not_friends"}
            return {"success": True, "message": "friend_removed"}

    async def block_user(
        self,
        user_id: str,
        blocked_user_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        """封鎖。一對人只能有一列：user_id＝先封鎖的人，mutual_block＝對方也封鎖了。

        封鎖前是好友就記 restore_on_unblock，最後一個人解除封鎖時恢復（LINE 式）。
        對方已經封鎖我時只標 mutual_block——以前整列換成我的封鎖，對方的封鎖就被抹掉了。
        """
        if user_id == blocked_user_id:
            return {"success": False, "error": "cannot_block_self"}

        async with using_session(session) as s:
            await _lock_pair(s, user_id, blocked_user_id)
            row = (
                await s.execute(
                    select(Friendship)
                    .where(_pair(user_id, blocked_user_id))
                    .with_for_update()
                )
            ).scalar_one_or_none()

            if row is not None and row.status == "blocked":
                if row.friend_id == user_id and not row.mutual_block:
                    row.mutual_block = True  # 對方先封鎖了我，現在我也封鎖對方
                    row.updated_at = datetime.now(timezone.utc)
                    await s.flush()
                # 我已經在封鎖（先封鎖的人或 mutual）：不動，旗標保留
                return {"success": True, "message": "user_blocked"}

            was_friend = row is not None and row.status == "accepted"
            if row is not None:
                await s.delete(row)  # 好友／邀請中／被拒的紀錄換成封鎖
                await s.flush()
            now = datetime.now(timezone.utc)
            s.add(
                Friendship(
                    user_id=user_id,
                    friend_id=blocked_user_id,
                    status="blocked",
                    restore_on_unblock=was_friend,
                    mutual_block=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            await s.flush()
            return {"success": True, "message": "user_blocked"}

    async def unblock_user(
        self,
        user_id: str,
        blocked_user_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        """解除封鎖。對方也在封鎖：這列改成只剩對方封鎖（旗標帶著走），不恢復；
        最後一個人解除時，封鎖前是好友就恢復好友。"""
        async with using_session(session) as s:
            await _lock_pair(s, user_id, blocked_user_id)
            row = (
                await s.execute(
                    select(Friendship)
                    .where(
                        _pair(user_id, blocked_user_id),
                        Friendship.status == "blocked",
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            i_blocked = row is not None and (row.user_id == user_id or row.mutual_block)
            if not i_blocked:
                return {"success": False, "error": "user_not_blocked"}

            now = datetime.now(timezone.utc)
            if row.mutual_block:
                # 對方還在封鎖：這列只留對方的封鎖
                row.user_id, row.friend_id = blocked_user_id, user_id
                row.mutual_block = False
                row.updated_at = now
                await s.flush()
                return {
                    "success": True,
                    "message": "user_unblocked",
                    "friendship_restored": False,
                }

            restore = row.restore_on_unblock
            await s.delete(row)
            await s.flush()
            if restore:
                s.add(
                    Friendship(
                        user_id=user_id,
                        friend_id=blocked_user_id,
                        status="accepted",
                        created_at=now,
                        updated_at=now,
                    )
                )
                await s.flush()
            return {
                "success": True,
                "message": "user_unblocked",
                "friendship_restored": restore,
            }

    async def get_blocked_users(
        self,
        user_id: str,
        limit: int = 100,
        session: AsyncSession | None = None,
    ) -> List[dict]:
        """我封鎖的人：我先封鎖的，加上互相封鎖時我是後封鎖的那方"""
        other_id = case(
            (Friendship.user_id == user_id, Friendship.friend_id),
            else_=Friendship.user_id,
        )
        stmt = (
            select(
                User.user_id,
                User.username,
                Friendship.created_at,
                User.display_name,
            )
            .join(User, User.user_id == other_id)
            .where(
                Friendship.status == "blocked",
                or_(
                    Friendship.user_id == user_id,
                    (Friendship.friend_id == user_id)
                    & Friendship.mutual_block.is_(True),
                ),
            )
            .order_by(Friendship.created_at.desc())
            .limit(limit)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.fetchall()
            return [
                {
                    "user_id": r[0],
                    "username": r[1],
                    "blocked_at": _fmt(r[2]),
                    "display_name": r[3],
                }
                for r in rows
            ]

    async def is_friend(
        self,
        user_id: str,
        other_user_id: str,
        session: AsyncSession | None = None,
    ) -> bool:
        stmt = (
            select(Friendship.id)
            .where(
                or_(
                    (Friendship.user_id == user_id)
                    & (Friendship.friend_id == other_user_id),
                    (Friendship.user_id == other_user_id)
                    & (Friendship.friend_id == user_id),
                ),
                Friendship.status == "accepted",
            )
            .limit(1)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def is_blocked(
        self,
        user_id: str,
        other_user_id: str,
        session: AsyncSession | None = None,
    ) -> bool:
        stmt = (
            select(Friendship.id)
            .where(
                or_(
                    (Friendship.user_id == user_id)
                    & (Friendship.friend_id == other_user_id),
                    (Friendship.user_id == other_user_id)
                    & (Friendship.friend_id == user_id),
                ),
                Friendship.status == "blocked",
            )
            .limit(1)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def get_friends_count(
        self,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(Friendship)
            .where(
                or_(
                    Friendship.user_id == user_id,
                    Friendship.friend_id == user_id,
                ),
                Friendship.status == "accepted",
            )
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            return result.scalar_one() or 0

    async def get_pending_count(
        self,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(Friendship)
            .where(
                Friendship.friend_id == user_id,
                Friendship.status == "pending",
            )
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            return result.scalar_one() or 0

    async def cancel_friend_request(
        self,
        user_id: str,
        target_user_id: str,
        session: AsyncSession | None = None,
    ) -> dict:
        stmt = delete(Friendship).where(
            Friendship.user_id == user_id,
            Friendship.friend_id == target_user_id,
            Friendship.status == "pending",
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            if result.rowcount == 0:
                return {"success": False, "error": "request_not_found"}
            return {"success": True, "message": "request_cancelled"}

    async def get_pending_requests_received(
        self,
        user_id: str,
        limit: int = 100,
        session: AsyncSession | None = None,
    ) -> List[dict]:
        stmt = (
            select(
                User.user_id,
                User.username,
                User.membership_tier,
                Friendship.id,
                Friendship.created_at,
                User.display_name,
            )
            .join(User, Friendship.user_id == User.user_id)
            .where(
                Friendship.friend_id == user_id,
                Friendship.status == "pending",
            )
            .order_by(Friendship.created_at.desc())
            .limit(limit)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.fetchall()
            return [
                {
                    "user_id": r[0],
                    "username": r[1],
                    "membership_tier": r[2] or "free",
                    "request_id": r[3],
                    "requested_at": _fmt(r[4]),
                    "display_name": r[5],
                }
                for r in rows
            ]

    async def get_pending_requests_sent(
        self,
        user_id: str,
        limit: int = 100,
        session: AsyncSession | None = None,
    ) -> List[dict]:
        stmt = (
            select(
                User.user_id,
                User.username,
                User.membership_tier,
                Friendship.id,
                Friendship.created_at,
                User.display_name,
            )
            .join(User, Friendship.friend_id == User.user_id)
            .where(
                Friendship.user_id == user_id,
                Friendship.status == "pending",
            )
            .order_by(Friendship.created_at.desc())
            .limit(limit)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.fetchall()
            return [
                {
                    "user_id": r[0],
                    "username": r[1],
                    "membership_tier": r[2] or "free",
                    "request_id": r[3],
                    "sent_at": _fmt(r[4]),
                    "display_name": r[5],
                }
                for r in rows
            ]

    async def get_bulk_friendship_status(
        self,
        user_id: str,
        other_user_ids: List[str],
        session: AsyncSession | None = None,
    ) -> dict:
        if not other_user_ids:
            return {}

        stmt = select(
            Friendship.id,
            Friendship.user_id,
            Friendship.friend_id,
            Friendship.status,
            Friendship.created_at,
            Friendship.updated_at,
            Friendship.mutual_block,
        ).where(
            or_(
                (Friendship.user_id == user_id)
                & (Friendship.friend_id.in_(other_user_ids)),
                (Friendship.friend_id == user_id)
                & (Friendship.user_id.in_(other_user_ids)),
            )
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.fetchall()
            result_map = {uid: None for uid in other_user_ids}

            for row in rows:
                uid1, uid2 = row[1], row[2]
                other = uid2 if uid1 == user_id else uid1
                result_map[other] = {
                    "id": row[0],
                    "requester_id": row[1],
                    "target_id": row[2],
                    "status": row[3],
                    "created_at": _fmt(row[4]),
                    "updated_at": _fmt(row[5]),
                    "is_requester": row[1] == user_id,
                    "blocked_by_me": _blocked_by(row[1], row[3], row[6], user_id),
                }

            return result_map

    async def get_public_user_profile(
        self,
        target_user_id: str,
        viewer_user_id: Optional[str] = None,
        session: AsyncSession | None = None,
    ) -> Optional[dict]:
        friendship_subq = None
        if viewer_user_id and viewer_user_id != target_user_id:
            friendship_subq = (
                select(
                    Friendship.id,
                    Friendship.user_id,
                    Friendship.friend_id,
                    Friendship.status,
                    Friendship.created_at,
                    Friendship.updated_at,
                    Friendship.mutual_block,
                )
                .where(
                    or_(
                        (Friendship.user_id == viewer_user_id)
                        & (Friendship.friend_id == target_user_id),
                        (Friendship.user_id == target_user_id)
                        & (Friendship.friend_id == viewer_user_id),
                    )
                )
                .limit(1)
                .subquery()
            )

        post_count_sq = (
            select(func.count())
            .select_from(Post)
            .where(Post.user_id == target_user_id, Post.is_hidden == 0)
            .correlate(None)
            .scalar_subquery()
        )

        total_pushes_sq = (
            select(func.coalesce(func.sum(Post.push_count), 0))
            .where(Post.user_id == target_user_id)
            .correlate(None)
            .scalar_subquery()
        )

        friends_count_sq = (
            select(func.count())
            .select_from(Friendship)
            .where(
                or_(
                    Friendship.user_id == target_user_id,
                    Friendship.friend_id == target_user_id,
                ),
                Friendship.status == "accepted",
            )
            .correlate(None)
            .scalar_subquery()
        )

        stmt = select(
            User.user_id,
            User.username,
            User.membership_tier,
            User.created_at,
            post_count_sq.label("post_count"),
            total_pushes_sq.label("total_pushes"),
            friends_count_sq.label("friends_count"),
            User.display_name.label("display_name"),
        ).where(User.user_id == target_user_id)

        if friendship_subq is not None:
            # LEFT JOIN ... ON TRUE：兩人之間沒有任何好友紀錄時子查詢是 0 列，
            # 以前直接 add_columns 等於 CROSS JOIN，整筆變 0 列 → 登入者看任何
            # 「非好友」的個人頁都 404（2026-09-26 論壇開放前盤查）
            stmt = stmt.select_from(User).outerjoin(friendship_subq, true())
            stmt = stmt.add_columns(
                friendship_subq.c.id.label("f_id"),
                friendship_subq.c.user_id.label("f_user_id"),
                friendship_subq.c.friend_id.label("f_friend_id"),
                friendship_subq.c.status.label("f_status"),
                friendship_subq.c.created_at.label("f_created_at"),
                friendship_subq.c.updated_at.label("f_updated_at"),
                friendship_subq.c.mutual_block.label("f_mutual_block"),
            )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            row = result.fetchone()
            if row is None:
                return None

            profile = {
                "user_id": row[0],
                "username": row[1],
                "membership_tier": row[2] or "free",
                "member_since": _fmt(row[3]),
                "post_count": row[4] or 0,
                "total_pushes": row[5] or 0,
                "friends_count": row[6] or 0,
                "display_name": row._mapping["display_name"],
                "is_friend": False,
                "friend_status": None,
                "is_requester": False,
                "blocked_by_me": False,
            }

            if friendship_subq is not None:
                # 用欄位名取：前面欄位增減時索引會跟著位移
                f_status = row._mapping["f_status"]
                f_user_id = row._mapping["f_user_id"]
                profile["friend_status"] = f_status
                profile["is_friend"] = f_status == "accepted" if f_status else False
                profile["is_requester"] = f_user_id == viewer_user_id
                profile["blocked_by_me"] = _blocked_by(
                    f_user_id, f_status, row._mapping["f_mutual_block"], viewer_user_id
                )

            return profile


friends_repo = FriendsRepository()
