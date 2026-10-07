"""
Async ORM repository for User operations.

This module provides async equivalents of the functions in core.database.user,
using SQLAlchemy 2.0 ORM models. It serves as the proof-of-concept for the
full ORM migration and can coexist with the raw SQL layer.

Usage::

    from core.orm.repositories import user_repo

    user = await user_repo.get_by_id("user-123")
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.onchain.addresses import wallet_identity_address

from .models import User, UserWallet
from .session import using_session

logger = logging.getLogger(__name__)


def _normalize_membership_tier(tier: Optional[str]) -> str:
    return (
        "premium"
        if (tier or "free").strip().lower() in {"premium", "plus", "pro"}
        else "free"
    )


def _is_membership_expired(expires_at) -> bool:
    """Check if a membership has expired. No expiry date = never expires."""
    if not expires_at:
        return False
    exp = expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= datetime.now(timezone.utc)


def _user_to_dict(user: User, has_bound_wallet: bool = False) -> dict:
    """Convert a User ORM object to a dict matching the legacy format."""
    # 有錢包＝身份本身是錢包（evm_…／舊 TON 地址）或 user_wallets 有綁定列。
    # 以前是 auth_method is not None：Telegram／Google 帳號也被當成有錢包。
    has_wallet = bool(wallet_identity_address(user.user_id)) or bool(has_bound_wallet)
    tier = _normalize_membership_tier(user.membership_tier)
    is_premium = tier == "premium" and not _is_membership_expired(
        user.membership_expires_at
    )
    # 對外 tier 一律輸出「有效等級」：analysis/agent_presets/agent_configs 等
    # 14+ 處 gate 讀 current_user 的 membership_tier，過期只翻 is_premium、
    # tier 保留 "premium" 會讓到期用戶繼續通過 Premium 檢查。
    effective_tier = "premium" if is_premium else "free"
    return {
        "user_id": user.user_id,
        "username": user.username,
        "auth_method": user.auth_method,
        "role": user.role or "user",
        "is_active": user.is_active if user.is_active is not None else True,
        "membership_tier": effective_tier,
        "membership_expires_at": (
            user.membership_expires_at.isoformat()
            if user.membership_expires_at
            else None
        ),
        "created_at": (user.created_at.isoformat() if user.created_at else None),
        "is_premium": is_premium,
        "has_wallet": has_wallet,
        # c010: 個人化暱稱（NULL = 未設定，前端 fallback 到 username）
        "display_name": getattr(user, "display_name", None),
    }


class UserRepository:
    """Async repository for User entity."""

    async def get_by_id(
        self, user_id: str, session: AsyncSession | None = None
    ) -> Optional[dict]:
        """Get user by ID, returning a dict in the legacy format."""
        has_bound_wallet = (
            exists().where(UserWallet.user_id == User.user_id).label("has_bound_wallet")
        )
        async with using_session(session) as s:
            result = await s.execute(
                select(User, has_bound_wallet).where(User.user_id == user_id)
            )
            row = result.one_or_none()
            if not row:
                return None
            return _user_to_dict(row[0], has_bound_wallet=bool(row[1]))

    async def get_language(
        self, user_id: str, session: AsyncSession | None = None
    ) -> Optional[str]:
        """Return the user's stored UI language preference, or None.

        Async equivalent of ``core.database.user.get_user_language`` — a
        single-column SELECT that avoids loading the whole row and avoids the
        sync thread pool (fixes the GET /api/user/me timeout caused by
        run_sync saturating the shared DB executor).
        Fault-tolerant like the legacy version: any error → None.
        """
        try:
            stmt = select(User.language).where(User.user_id == user_id)
            async with using_session(session) as s:
                row = (await s.execute(stmt)).scalar_one_or_none()
                return row or None
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # noqa: BLE001 — 與 legacy 一致容錯
            logger.warning("UserRepository.get_language unavailable: %s", e)
            return None

    async def get_display_name(
        self, user_id: str, session: AsyncSession | None = None
    ) -> Optional[str]:
        """Return the user's personalized display name, or None.

        Async equivalent of ``core.database.user.get_user_display_name``.
        Fault-tolerant like the legacy version: any error → None.
        """
        try:
            stmt = select(User.display_name).where(User.user_id == user_id)
            async with using_session(session) as s:
                row = (await s.execute(stmt)).scalar_one_or_none()
                return row or None
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # noqa: BLE001 — 與 legacy 一致容錯
            logger.warning("UserRepository.get_display_name unavailable: %s", e)
            return None

    async def get_by_username(
        self, username: str, session: AsyncSession | None = None
    ) -> Optional[dict]:
        """Get user by username."""
        async with using_session(session) as s:
            result = await s.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            return _user_to_dict(user) if user else None

    async def update_last_active(
        self, user_id: str, session: AsyncSession | None = None
    ) -> bool:
        """Update user's last active timestamp."""
        async with using_session(session) as s:
            result = await s.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(last_active_at=datetime.now(timezone.utc))
            )
            return result.rowcount > 0

    async def get_membership(
        self, user_id: str, session: AsyncSession | None = None
    ) -> dict:
        """Get user membership info."""
        user = await self.get_by_id(user_id, session)
        if not user:
            return {
                "is_premium": False,
                "membership_tier": "free",
                "days_remaining": 0,
                "expires_at": None,
            }
        return {
            "is_premium": user["is_premium"],
            "membership_tier": user["membership_tier"],
            "days_remaining": self._days_remaining(user.get("membership_expires_at")),
            # 前端（SPA 徽章／論壇 premium 頁續訂提示）依 expires_at 顯示到期日
            # ——2026-09-10 前從未回傳，所有到期日 UI 都是死代碼（三份 code
            # review 交叉確認）。格式＝get_by_id 的 isoformat 字串。
            "expires_at": user.get("membership_expires_at"),
        }

    @staticmethod
    def _days_remaining(expires_at: Optional[str]) -> int:
        if not expires_at:
            return 0
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            delta = exp - datetime.now(timezone.utc)
            return max(0, delta.days)
        except (ValueError, TypeError):
            return 0


user_repo = UserRepository()
