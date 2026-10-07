"""
Async ORM repository for UserAnalysisPreference operations.

Provides async CRUD operations for user analysis preferences per agent,
including system_prompt and enabled_tools per user/agent combination.

Usage::

    from core.orm.analysis_preferences_repo import analysis_preferences_repo

    prefs = await analysis_preferences_repo.get_preferences("user-1", "crypto")
    all_prefs = await analysis_preferences_repo.get_all_preferences("user-1")
    result = await analysis_preferences_repo.upsert_preference(
        "user-1", "crypto", "You are a conservative investor.", ["get_crypto_price", "technical_analysis"]
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserAnalysisPreference

logger = logging.getLogger(__name__)


class AnalysisPreferencesRepository:
    """Async ORM repository for user analysis preferences."""

    async def get_preferences(
        self,
        db: AsyncSession,
        user_id: str,
        agent_id: str,
    ) -> Optional[UserAnalysisPreference]:
        """Get a single preference entry for user_id + agent_id."""
        stmt = select(UserAnalysisPreference).where(
            and_(
                UserAnalysisPreference.user_id == user_id,
                UserAnalysisPreference.agent_id == agent_id,
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_preferences(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> list[UserAnalysisPreference]:
        """Get all agent preferences for a given user."""
        stmt = (
            select(UserAnalysisPreference)
            .where(UserAnalysisPreference.user_id == user_id)
            .order_by(UserAnalysisPreference.agent_id)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def upsert_preference(
        self,
        db: AsyncSession,
        user_id: str,
        agent_id: str,
        system_prompt: Optional[str],
        enabled_tools: Optional[list[str]],
    ) -> UserAnalysisPreference:
        """Insert or update a preference entry."""
        now = datetime.now(timezone.utc)
        stmt = (
            pg_insert(UserAnalysisPreference)
            .values(
                user_id=user_id,
                agent_id=agent_id,
                system_prompt=system_prompt,
                enabled_tools=enabled_tools,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "agent_id"],
                set_={
                    "system_prompt": pg_insert.excluded.system_prompt,
                    "enabled_tools": pg_insert.excluded.enabled_tools,
                    "updated_at": now,
                },
            )
            .returning(UserAnalysisPreference)
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.scalar_one()

    async def delete_preference(
        self,
        db: AsyncSession,
        user_id: str,
        agent_id: str,
    ) -> bool:
        """Delete a preference entry. Returns True if a row was deleted."""
        stmt = UserAnalysisPreference.__table__.delete().where(
            and_(
                UserAnalysisPreference.user_id == user_id,
                UserAnalysisPreference.agent_id == agent_id,
            )
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0


analysis_preferences_repo = AnalysisPreferencesRepository()
