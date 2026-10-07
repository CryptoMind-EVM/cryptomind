"""Async ORM repository for user favorites（探索介面收藏，impl plan Task D3）.

Priority 1 輕量標記：單次收藏／取消／列表；無背景工作、無通知
（「追蹤更新」屬 Phase 5 Watchlist）。Pilot 期間不限額（決策 12）。
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserFavorite

# Phase 1 擴充 oc_collective（design 2026-08-17-discover-multisource）；
# DB 側 CHECK constraint 由 alembic c028 同步放寬。
VALID_ITEM_TYPES = ("manifund_project", "manifund_user", "oc_collective")


def new_favorite_id() -> str:
    return f"fav_{uuid.uuid4().hex}"


class FavoritesRepository:
    """Async ORM repository for user_favorites."""

    async def list_favorites(
        self, db: AsyncSession, user_id: str, item_type: Optional[str] = None
    ) -> List[UserFavorite]:
        stmt = (
            select(UserFavorite)
            .where(UserFavorite.user_id == user_id)
            .order_by(UserFavorite.created_at.desc())
        )
        if item_type:
            stmt = stmt.where(UserFavorite.item_type == item_type)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def add_favorite(
        self,
        db: AsyncSession,
        user_id: str,
        item_type: str,
        item_id: str,
        source_url: Optional[str] = None,
    ) -> UserFavorite:
        """冪等收藏（重複收藏同一項目 = no-op 回既有列）。"""
        fav_id = new_favorite_id()
        stmt = (
            pg_insert(UserFavorite)
            .values(
                fav_id=fav_id,
                user_id=user_id,
                item_type=item_type,
                item_id=item_id,
                source_url=source_url,
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "item_type", "item_id"]
            )
            .returning(UserFavorite)
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            # 已存在：回既有列
            existing = await self.get_favorite(db, user_id, item_type, item_id)
            if existing is not None:
                return existing
            row = await db.get(UserFavorite, fav_id)
        await db.commit()
        return row

    async def get_favorite(
        self, db: AsyncSession, user_id: str, item_type: str, item_id: str
    ) -> Optional[UserFavorite]:
        stmt = select(UserFavorite).where(
            UserFavorite.user_id == user_id,
            UserFavorite.item_type == item_type,
            UserFavorite.item_id == item_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def remove_favorite(
        self, db: AsyncSession, user_id: str, item_type: str, item_id: str
    ) -> bool:
        stmt = delete(UserFavorite).where(
            UserFavorite.user_id == user_id,
            UserFavorite.item_type == item_type,
            UserFavorite.item_id == item_id,
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0


favorites_repo = FavoritesRepository()
