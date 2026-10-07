"""Async ORM repository for user agent configs（Model Mixer Step 1）.

語義：**無列 = 官方預設**（design §2「未設定 → 官方預設組合」），不需 seed。
``agent_id`` 合法性由 API 層對 ProfileCatalog 驗證（repo 不重複檢查）。
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserAgentConfig


class _Unset(Enum):
    VALUE = "unset"


UNSET = _Unset.VALUE


class AgentConfigsRepository:
    """Async ORM repository for user_agent_configs."""

    async def list_configs(
        self, db: AsyncSession, preset_id: str
    ) -> List[UserAgentConfig]:
        """c044 起是 **preset 範圍**——沒有使用者層、沒有 fallback。"""
        stmt = (
            select(UserAgentConfig)
            .where(UserAgentConfig.preset_id == preset_id)
            .order_by(UserAgentConfig.agent_id)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_config(
        self, db: AsyncSession, preset_id: str, agent_id: str
    ) -> Optional[UserAgentConfig]:
        stmt = select(UserAgentConfig).where(
            UserAgentConfig.preset_id == preset_id,
            UserAgentConfig.agent_id == agent_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def set_config(
        self,
        db: AsyncSession,
        preset_id: str,
        agent_id: str,
        user_id: str,
        model_selection: Optional[dict] | _Unset = UNSET,
        tools: List[str] | _Unset = UNSET,
        skills: List[str] | _Unset = UNSET,
        system_prompt: Optional[str] | _Unset = UNSET,
    ) -> UserAgentConfig:
        """Upsert（Mixer Step 2 起同時寫 model_selection 與 tools；Step 4 加 skills；
        2026-09-09 加 per-agent system_prompt）。

        ``model_selection=None`` 表示重設為官方預設、``tools=[]`` / ``skills=[]``
        表示未設定（官方預設池）、``system_prompt=None`` 表示重設為未設定
        ——列保留，供其他欄繼續使用。未提供的欄位保留 UNSET，更新與首寫
        衝突重試都只套用本次明確提供的欄位。

        採 ORM unit-of-work（載入實例 → 屬性賦值 → commit），欄位值經
        SQLAlchemy 參數化寫入。兩個請求同時首寫同一列時，後 commit 者撞
        ``PRIMARY KEY (preset_id, agent_id)`` → rollback 後重讀再寫（後寫者勝，
        不噴 500）。**刻意只重試一次**：第二次仍失敗代表非首寫競態的真實
        約束違反（或列在重讀後又被刪除的極罕見交錯），讓例外自然上拋
        供日誌觀測，不做無界重試。
        """
        config = await self.get_config(db, preset_id, agent_id)
        if config is None:
            config = UserAgentConfig(
                preset_id=preset_id, agent_id=agent_id, user_id=user_id,
                model_selection=None, tools=[], skills=[],
            )
            db.add(config)
        if model_selection is not UNSET:
            config.model_selection = model_selection
        if tools is not UNSET:
            config.tools = list(tools)
        if skills is not UNSET:
            config.skills = list(skills)
        if system_prompt is not UNSET:
            config.system_prompt = system_prompt
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            config = await self.get_config(db, preset_id, agent_id)
            if config is None:
                raise
            if model_selection is not UNSET:
                config.model_selection = model_selection
            if tools is not UNSET:
                config.tools = list(tools)
            if skills is not UNSET:
                config.skills = list(skills)
            if system_prompt is not UNSET:
                config.system_prompt = system_prompt
            await db.commit()
        await db.refresh(config)
        return config

    async def delete_config(
        self, db: AsyncSession, preset_id: str, agent_id: str
    ) -> bool:
        """整列刪除＝這個 preset 的該 agent 完全回到官方預設。"""
        stmt = delete(UserAgentConfig).where(
            UserAgentConfig.preset_id == preset_id,
            UserAgentConfig.agent_id == agent_id,
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0


agent_configs_repo = AgentConfigsRepository()
