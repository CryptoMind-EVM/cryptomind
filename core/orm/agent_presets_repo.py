"""Async ORM repository for user agent presets（impl plan Part B）.

上限規則：每位使用者最多 ``MAX_PRESETS_PER_USER`` 個 preset（超額由 API 層
回 409）；``agent_ids`` 已由 API 層經 ProfileCatalog 驗證。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserAgentPreset

MAX_PRESETS_PER_USER = 10


def new_preset_id() -> str:
    return f"prst_{uuid.uuid4().hex}"


class AgentPresetsRepository:
    """Async ORM repository for user_agent_presets."""

    async def list_presets(
        self, db: AsyncSession, user_id: str
    ) -> List[UserAgentPreset]:
        stmt = (
            select(UserAgentPreset)
            .where(UserAgentPreset.user_id == user_id)
            .order_by(UserAgentPreset.created_at)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_preset(
        self, db: AsyncSession, user_id: str, preset_id: str
    ) -> Optional[UserAgentPreset]:
        """Ownership-scoped single lookup（他人 preset 一律查不到）。"""
        stmt = select(UserAgentPreset).where(
            UserAgentPreset.preset_id == preset_id,
            UserAgentPreset.user_id == user_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_default_preset(
        self, db: AsyncSession, user_id: str
    ) -> Optional[UserAgentPreset]:
        stmt = (
            select(UserAgentPreset)
            .where(
                UserAgentPreset.user_id == user_id,
                UserAgentPreset.is_default.is_(True),
            )
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def count_presets(self, db: AsyncSession, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(UserAgentPreset)
            .where(UserAgentPreset.user_id == user_id)
        )
        result = await db.execute(stmt)
        return int(result.scalar() or 0)

    async def create_preset(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        name: str,
        mode: str,
        agent_ids: List[str],
        analysis_mode: str,
        action_policy: str,
        capability_overrides: dict,
        is_default: bool,
        config_version: str,
    ) -> UserAgentPreset:
        preset = UserAgentPreset(
            preset_id=new_preset_id(),
            user_id=user_id,
            name=name,
            mode=mode,
            agent_ids=agent_ids,
            analysis_mode=analysis_mode,
            action_policy=action_policy,
            capability_overrides=capability_overrides,
            is_default=False,
            config_version=config_version,
        )
        db.add(preset)
        if is_default:
            await self._clear_default(db, user_id, exclude_id=None)
            preset.is_default = True
        await db.commit()
        await db.refresh(preset)
        return preset

    async def update_preset(
        self, db: AsyncSession, preset: UserAgentPreset, **fields
    ) -> UserAgentPreset:
        for key in (
            "name",
            "mode",
            "agent_ids",
            "analysis_mode",
            "action_policy",
            "capability_overrides",
        ):
            if key in fields and fields[key] is not None:
                setattr(preset, key, fields[key])
        preset.updated_at = datetime.now(timezone.utc)
        db.add(preset)
        await db.commit()
        await db.refresh(preset)
        return preset

    async def delete_preset(
        self, db: AsyncSession, user_id: str, preset_id: str
    ) -> bool:
        stmt = delete(UserAgentPreset).where(
            UserAgentPreset.preset_id == preset_id,
            UserAgentPreset.user_id == user_id,
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0

    async def set_default(
        self, db: AsyncSession, user_id: str, preset_id: str
    ) -> bool:
        """交易內切換 default（清舊 → 設新）。"""
        preset = await self.get_preset(db, user_id, preset_id)
        if preset is None:
            return False
        await self._clear_default(db, user_id, exclude_id=preset_id)
        stmt = (
            update(UserAgentPreset)
            .where(UserAgentPreset.preset_id == preset_id)
            .values(is_default=True, updated_at=datetime.now(timezone.utc))
        )
        await db.execute(stmt)
        await db.commit()
        return True

    async def _clear_default(
        self, db: AsyncSession, user_id: str, exclude_id: Optional[str]
    ) -> None:
        # .values() 不能省：少了它，這句會把整列欄位綁 None、舊 default
        # 清不掉，set_default 隨即撞上唯一 default 約束（2026-09-05 線上 P0，
        # 切換 preset 必 500）。回歸與守衛：tests/test_agent_presets_default_switch.py
        stmt = (
            update(UserAgentPreset)
            .values(is_default=False, updated_at=datetime.now(timezone.utc))
            .where(
                UserAgentPreset.user_id == user_id,
                UserAgentPreset.is_default.is_(True),
            )
        )
        if exclude_id is not None:
            stmt = stmt.where(UserAgentPreset.preset_id != exclude_id)
        await db.execute(stmt)


agent_presets_repo = AgentPresetsRepository()
