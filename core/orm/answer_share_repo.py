"""
AI 回答快照分享的資料層（c072；core/answer_share.py 是純函式、這裡只管存取）。

- token 只在建立當下回給呼叫端一次，資料庫只有它的 SHA-256。
- 公開讀取（get_active）只認「沒撤銷、沒過期」的列；token 格式不對直接回 None，不進資料庫。
- 撤銷一律帶 user_id：只有自己的撤得掉（別人的 id 回 False，呼叫端當 404，不洩漏存在與否）。
- 每日上限的計數包含已撤銷的列：不能靠「分享→撤銷」繞過上限。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core import answer_share as core

from .models import SharedAnswer as Row
from .session import using_session

_PURGE_GRACE = timedelta(days=7)
_LIST_LIMIT = core.MAX_ACTIVE  # 清單要放得下所有有效連結，否則放不進清單的撤銷不了


class ShareLimitError(Exception):
    """超過每日建立上限（kind='daily'）或有效連結總量上限（kind='active'）。"""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class AnswerShareRepository:
    async def create(
        self,
        user_id: str,
        question: str,
        answer: str,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
        daily_limit: Optional[int] = None,
        max_active: Optional[int] = None,
    ) -> dict:
        """存一份快照，回 {id, token, expires_at}。token 只有這一次拿得到。

        有給 daily_limit／max_active 時，檢查與寫入在同一個交易、同一把 advisory lock 底下：
        並行的請求會排隊，不會「都看到 19、全部建立成功」。超過丟 ShareLimitError。
        """
        now = now or datetime.now(timezone.utc)
        token = core.new_token()
        expires_at = now + timedelta(days=core.TTL_DAYS)
        async with using_session(session) as s:
            if daily_limit is not None or max_active is not None:
                await s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"answer_share:{user_id}"},
                )
                if (
                    daily_limit is not None
                    and await self.count_recent(user_id, now=now, session=s)
                    >= daily_limit
                ):
                    raise ShareLimitError("daily")
                if (
                    max_active is not None
                    and await self.count_active(user_id, now=now, session=s)
                    >= max_active
                ):
                    raise ShareLimitError("active")
            row = Row(
                token_hash=core.hash_token(token),
                user_id=user_id,
                question=question,
                answer=answer,
                created_at=now,
                expires_at=expires_at,
            )
            s.add(row)
            await s.flush()
            return {"id": row.id, "token": token, "expires_at": expires_at}

    async def get_active(
        self,
        token: str,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> Optional[dict]:
        if not core.valid_token_shape(token):
            return None
        now = now or datetime.now(timezone.utc)
        async with using_session(session) as s:
            row = await s.scalar(
                select(Row).where(
                    Row.token_hash == core.hash_token(token),
                    Row.revoked_at.is_(None),
                    Row.expires_at > now,
                )
            )
            if row is None:
                return None
            return {
                "question": row.question,
                "answer": row.answer,
                "created_at": row.created_at,
            }

    async def list_active(
        self,
        user_id: str,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> list[dict]:
        """自己還有效的分享，新到舊。不含 token／hash（連結只在建立當下給一次）。"""
        now = now or datetime.now(timezone.utc)
        async with using_session(session) as s:
            rows = (
                await s.scalars(
                    select(Row)
                    .where(
                        Row.user_id == user_id,
                        Row.revoked_at.is_(None),
                        Row.expires_at > now,
                    )
                    .order_by(Row.id.desc())
                    .limit(_LIST_LIMIT)
                )
            ).all()
        return [
            {
                "id": r.id,
                "question": r.question,
                "created_at": r.created_at,
                "expires_at": r.expires_at,
            }
            for r in rows
        ]

    async def revoke(
        self,
        user_id: str,
        share_id: int,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        async with using_session(session) as s:
            result = await s.execute(
                update(Row)
                .where(
                    Row.id == share_id,
                    Row.user_id == user_id,
                    Row.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            return result.rowcount == 1

    async def count_recent(
        self,
        user_id: str,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> int:
        """過去 24 小時建立的份數（含已撤銷）——每日上限用。"""
        now = now or datetime.now(timezone.utc)
        async with using_session(session) as s:
            return int(
                await s.scalar(
                    select(func.count())
                    .select_from(Row)
                    .where(
                        Row.user_id == user_id,
                        Row.created_at >= now - timedelta(days=1),
                    )
                )
                or 0
            )

    async def count_active(
        self,
        user_id: str,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> int:
        """目前還有效（沒撤銷、沒過期）的連結數——總量上限用。"""
        now = now or datetime.now(timezone.utc)
        async with using_session(session) as s:
            return int(
                await s.scalar(
                    select(func.count())
                    .select_from(Row)
                    .where(
                        Row.user_id == user_id,
                        Row.revoked_at.is_(None),
                        Row.expires_at > now,
                    )
                )
                or 0
            )

    async def purge_expired(
        self,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> int:
        """刪掉過期或撤銷超過一週的列（連結早就失效，內容不必留著）。回刪掉幾列。"""
        now = now or datetime.now(timezone.utc)
        cutoff = now - _PURGE_GRACE
        async with using_session(session) as s:
            result = await s.execute(
                delete(Row).where(or_(Row.expires_at < cutoff, Row.revoked_at < cutoff))
            )
            return result.rowcount or 0


answer_share_repo = AnswerShareRepository()
