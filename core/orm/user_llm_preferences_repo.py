"""
Async ORM repository for the user's selected LLM provider (cross-device recall).

Usage::

    from core.orm.user_llm_preferences_repo import user_llm_preferences_repo

    await user_llm_preferences_repo.set_selected_provider("user-1", "deepseek")
    provider = await user_llm_preferences_repo.get_selected_provider("user-1")

Design: docs/plans/2026-08-06-persist-user-selected-provider-design.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserLLMPreference
from .session import using_session

logger = logging.getLogger(__name__)


class UserLLMPreferencesRepo:
    """Per-user selected LLM provider (single row, upsert by user_id)."""

    async def get_selected_provider(
        self,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> Optional[str]:
        """Return the user's selected provider, or None if never set."""
        stmt = select(UserLLMPreference.provider).where(
            UserLLMPreference.user_id == user_id
        )
        async with using_session(session) as s:
            result = await s.execute(stmt)
            row = result.scalar_one_or_none()
            return row or None

    async def set_selected_provider(
        self,
        user_id: str,
        provider: str,
        session: AsyncSession | None = None,
    ) -> None:
        """Upsert the user's selected provider (insert or update on conflict).

        Note: we do NOT validate that the user has a key for ``provider`` here —
        the caller (front-end switch / API) may set a preference before binding a
        key. ``getCurrentProvider`` on the client still falls back to any
        provider that has a key when the selected one is unusable.
        """
        async with using_session(session) as s:
            now = datetime.now(timezone.utc)
            stmt = pg_insert(UserLLMPreference).values(
                user_id=user_id,
                provider=provider,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[UserLLMPreference.user_id],
                set_={"provider": provider, "updated_at": now},
            )
            await s.execute(stmt)
            await s.commit()


user_llm_preferences_repo = UserLLMPreferencesRepo()
