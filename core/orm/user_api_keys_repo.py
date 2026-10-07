"""
Async ORM repository for User API Key operations.

Usage::

    from core.orm.user_api_keys_repo import user_api_keys_repo

    result = await user_api_keys_repo.save_user_api_key("user-1", "openai", "sk-...")
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.model_config import MODEL_CONFIG
from utils.encryption import (
    decrypt_api_key,
    encrypt_api_key,
    is_legacy_ciphertext,
    mask_api_key,
)

from .models import UserApiKey, UserSavedModel
from .session import using_session

logger = logging.getLogger(__name__)

# LLM 供應商（聊天大模型，key_kind="llm"）。
# 直接衍生自 MODEL_CONFIG（＝前端 UI 顯示、且 validate-key 接受的 provider 集合），
# 不再手寫清單。過去手寫版本漏了 nvidia/minimax/volcengine/moonshot/dashscope/zhipu，
# 導致「測試通過但存檔回 Unsupported provider」。用單一真實來源杜絕漂移。
LLM_PROVIDERS = list(MODEL_CONFIG.keys())


def platform_binding(provider: str) -> Optional[Dict[str, Any]]:
    """keyless provider（平台自架模型）的合成綁定：不進 user_api_keys 表，每個使用者預設就有。

    回 {"api_key": 佔位, "model": 品牌別名}；非 keyless 或平台模型未啟用回 None。
    所有金鑰讀取（get_user_api_key* / get_all_user_api_keys）先走這裡，Agent 配置、
    Router、聊天都不用各自特判。
    """
    from core.model_config import is_keyless_provider

    if not is_keyless_provider(provider):
        return None
    from core.agents.fallback import platform_model_enabled

    if not platform_model_enabled():
        return None
    return {"api_key": "local", "model": MODEL_CONFIG[provider]["default_model"]}


# 工具供應商（BYOK 第三方工具金鑰，key_kind="tool"）
TOOL_PROVIDERS = [
    "tavily",
    "serper",
    "fmp",
    "fred",
    "coinmarketcap",
    "etherscan",
    "cryptopanic",
    "newsapi",
]

# 工具供應商的前端顯示資訊（名稱、用途、申請金鑰連結）
TOOL_PROVIDER_META = {
    "tavily": {
        "display_name": "Tavily Search",
        "description": "提升網路搜尋品質（未設定則使用平台自架的聚合搜尋）",
        "signup_url": "https://app.tavily.com/",
    },
    "serper": {
        "display_name": "Serper（Google 搜尋）",
        "description": "用你自己的 Google 搜尋配額跑 web_search，中文品質最佳（免費 2500 次/月，免信用卡）",
        "signup_url": "https://serper.dev/",
    },
    "fmp": {
        "display_name": "Financial Modeling Prep",
        "description": "用公司名查股票代號，支援中文（如「台積電」→ TSM）。免費 250 次/天",
        "signup_url": "https://site.financialmodelingprep.com/developer/docs",
    },
    "fred": {
        "display_name": "FRED（聖路易聯準會）",
        "description": "央行利率與總體經濟數據（免費申請）",
        "signup_url": "https://fredaccount.stlouisfed.org/apikeys",
    },
    "coinmarketcap": {
        "display_name": "CoinMarketCap",
        "description": "加密貨幣行情、市值與排名資料",
        "signup_url": "https://pro.coinmarketcap.com/account",
    },
    "etherscan": {
        "display_name": "Etherscan",
        "description": "以太坊鏈上資料：地址餘額、Gas、交易",
        "signup_url": "https://etherscan.io/myapikey",
    },
    "cryptopanic": {
        "display_name": "CryptoPanic",
        "description": "專業加密貨幣新聞聚合（設定後 aggregate_news 可獲取更即時的新聞）",
        "signup_url": "https://cryptopanic.com/developers/api/",
    },
    "newsapi": {
        "display_name": "NewsAPI",
        "description": "主流媒體新聞來源（免費版 100 請求/天，設定後 aggregate_news 可使用）",
        "signup_url": "https://newsapi.org/register",
    },
}


def tool_provider_meta() -> Dict[str, Dict[str, Any]]:
    """Return frontend-facing metadata for all tool (BYOK) providers."""
    return {
        provider: {"provider": provider, **TOOL_PROVIDER_META.get(provider, {})}
        for provider in TOOL_PROVIDERS
    }


SUPPORTED_PROVIDERS = LLM_PROVIDERS + TOOL_PROVIDERS

# provider -> key_kind 對照，用於儲存與過濾
_PROVIDER_KIND = {
    **{p: "llm" for p in LLM_PROVIDERS},
    **{p: "tool" for p in TOOL_PROVIDERS},
}


def provider_kind(provider: str) -> str:
    """Return the key_kind ('llm' | 'tool') for a provider, defaulting to 'llm'."""
    return _PROVIDER_KIND.get(provider, "llm")


async def _decrypt_and_upgrade(
    s: AsyncSession, user_id: str, provider: str, stored: str
) -> str:
    """解密；舊格式（固定 salt）解得開就在同一個 session 內改寫成 v2。

    compare-and-swap（WHERE encrypted_key = 剛讀到的舊值）：使用者同時存了新金鑰
    就不會被蓋掉；updated_at 維持原值（不是使用者動作）。解不開的列不寫；改寫
    失敗只記 log、讀取照常——舊格式永遠解得開，下次讀取再升級。

    v2 每筆 salt 都要跑一次 PBKDF2（~0.2s，會放開 GIL）→ 丟 thread，不卡 event loop。
    """
    decrypted = await asyncio.to_thread(decrypt_api_key, stored)
    if not decrypted or not is_legacy_ciphertext(stored):
        return decrypted
    try:
        upgraded = await asyncio.to_thread(encrypt_api_key, decrypted)
        async with s.begin_nested():
            await s.execute(
                update(UserApiKey)
                .where(
                    UserApiKey.user_id == user_id,
                    UserApiKey.provider == provider,
                    UserApiKey.encrypted_key == stored,
                )
                .values(encrypted_key=upgraded, updated_at=UserApiKey.updated_at)
            )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning(
            "BYOK key upgrade to v2 skipped (provider=%s): %s",
            provider,
            type(exc).__name__,
        )
    return decrypted


def _saved_model_insert(user_id: str, provider: str, model: str):
    """INSERT 一筆保存模型，(user_id, provider, model) 已存在時忽略。"""
    stmt = pg_insert(UserSavedModel).values(
        user_id=user_id, provider=provider, model=model
    )
    return stmt.on_conflict_do_nothing(index_elements=["user_id", "provider", "model"])


class UserApiKeysRepository:
    """Async ORM repository for user API key management."""

    async def save_user_api_key(
        self,
        user_id: str,
        provider: str,
        api_key: str,
        model: Optional[str] = None,
        session: AsyncSession | None = None,
    ) -> Dict[str, Any]:
        """Save (upsert) a user's API key (encrypted)."""
        if provider not in SUPPORTED_PROVIDERS:
            return {"success": False, "error": f"Unsupported provider: {provider}"}

        if not api_key or not api_key.strip():
            return {"success": False, "error": "API key cannot be empty"}

        encrypted_key = await asyncio.to_thread(encrypt_api_key, api_key.strip())
        now = datetime.now(timezone.utc)
        kind = provider_kind(provider)

        stmt = pg_insert(UserApiKey).values(
            user_id=user_id,
            provider=provider,
            encrypted_key=encrypted_key,
            model_selection=model,
            key_kind=kind,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "provider"],
            set_={
                "encrypted_key": stmt.excluded.encrypted_key,
                "model_selection": stmt.excluded.model_selection,
                "key_kind": stmt.excluded.key_kind,
                "updated_at": now,
            },
        )

        model_name = (model or "").strip()

        async with using_session(session) as s:
            await s.execute(stmt)
            # 同 provider 換模型不再互相覆蓋：模型記進 user_saved_models 清單
            # （同 provider + 同 model 重綁只是換金鑰，清單不重複）
            if kind == "llm" and model_name:
                await s.execute(_saved_model_insert(user_id, provider, model_name))

        return {"success": True}

    async def add_saved_model(
        self,
        user_id: str,
        provider: str,
        model: str,
        session: AsyncSession | None = None,
    ) -> Dict[str, Any]:
        """Record a (provider, model) pair in the user's saved-models list."""
        model_name = (model or "").strip()
        if not model_name:
            return {"success": False, "error": "Model cannot be empty"}

        async with using_session(session) as s:
            await s.execute(_saved_model_insert(user_id, provider, model_name))

        return {"success": True}

    async def delete_saved_model(
        self,
        user_id: str,
        provider: str,
        model: str,
        session: AsyncSession | None = None,
    ) -> Dict[str, Any]:
        """Remove one saved (provider, model) entry.

        刪掉 provider 最後一個模型 = 整個 provider 解綁（金鑰列一併刪除）；
        刪掉使用中的模型則把 model_selection 改指到最近保存的另一個模型。
        """
        async with using_session(session) as s:
            deleted = await s.execute(
                delete(UserSavedModel).where(
                    UserSavedModel.user_id == user_id,
                    UserSavedModel.provider == provider,
                    UserSavedModel.model == model,
                )
            )
            if deleted.rowcount == 0:
                return {"success": False, "error": "Model binding not found"}

            remaining_rows = (
                await s.execute(
                    select(UserSavedModel.model)
                    .where(
                        UserSavedModel.user_id == user_id,
                        UserSavedModel.provider == provider,
                    )
                    .order_by(
                        UserSavedModel.created_at.desc(), UserSavedModel.id.desc()
                    )
                )
            ).fetchall()

            if not remaining_rows:
                await s.execute(
                    delete(UserApiKey).where(
                        UserApiKey.user_id == user_id,
                        UserApiKey.provider == provider,
                    )
                )
                return {"success": True, "provider_removed": True}

            active_model = (
                await s.execute(
                    select(UserApiKey.model_selection).where(
                        UserApiKey.user_id == user_id,
                        UserApiKey.provider == provider,
                    )
                )
            ).scalar_one_or_none()

            if active_model == model:
                active_model = remaining_rows[0][0]
                await s.execute(
                    update(UserApiKey)
                    .where(
                        UserApiKey.user_id == user_id,
                        UserApiKey.provider == provider,
                    )
                    .values(
                        model_selection=active_model,
                        updated_at=datetime.now(timezone.utc),
                    )
                )

            return {
                "success": True,
                "provider_removed": False,
                "active_model": active_model,
            }

    async def get_user_api_key(
        self,
        user_id: str,
        provider: str,
        session: AsyncSession | None = None,
    ) -> Optional[str]:
        """Get the decrypted API key for a user + provider, or None."""
        pb = platform_binding(provider)
        if pb:
            return pb["api_key"]
        stmt = select(UserApiKey.encrypted_key).where(
            UserApiKey.user_id == user_id,
            UserApiKey.provider == provider,
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            row = result.scalar_one_or_none()
            if not row:
                return None
            decrypted = await _decrypt_and_upgrade(s, user_id, provider, row)
            return decrypted or None

    async def get_user_api_key_with_model(
        self,
        user_id: str,
        provider: str,
        session: AsyncSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Get decrypted API key + model_selection for a user + provider.

        Returns {"api_key": "...", "model": "..."} or None.
        """
        pb = platform_binding(provider)
        if pb:
            return {"api_key": pb["api_key"], "model": pb["model"]}
        stmt = select(
            UserApiKey.encrypted_key,
            UserApiKey.model_selection,
        ).where(
            UserApiKey.user_id == user_id,
            UserApiKey.provider == provider,
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            row = result.fetchone()
            if not row:
                return None
            decrypted = await _decrypt_and_upgrade(s, user_id, provider, row[0])
            if not decrypted:
                return None
            return {"api_key": decrypted, "model": row[1]}

    async def get_user_api_key_masked(
        self,
        user_id: str,
        provider: str,
        session: AsyncSession | None = None,
    ) -> Dict[str, Any]:
        """Get a masked version of the API key for frontend display."""
        pb = platform_binding(provider)
        if pb:
            return {
                "has_key": True,
                "masked_key": None,
                "model": pb["model"],
                "updated_at": None,
                "platform": True,
            }
        stmt = select(
            UserApiKey.encrypted_key,
            UserApiKey.model_selection,
            UserApiKey.updated_at,
        ).where(
            UserApiKey.user_id == user_id,
            UserApiKey.provider == provider,
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            row = result.fetchone()
            if not row:
                return {
                    "has_key": False,
                    "masked_key": None,
                    "model": None,
                    "updated_at": None,
                }

            decrypted = await _decrypt_and_upgrade(s, user_id, provider, row[0])
            masked = mask_api_key(decrypted) if decrypted else None

            return {
                "has_key": bool(decrypted),
                "masked_key": masked,
                "model": row[1],
                "updated_at": row[2].isoformat() if row[2] else None,
                "corrupted": not bool(decrypted),
            }

    async def get_all_user_api_keys(
        self,
        user_id: str,
        kind: Optional[str] = None,
        session: AsyncSession | None = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Get masked versions of a user's API keys.

        When ``kind`` is "llm" or "tool", only providers of that kind are
        returned (and only those providers get default empty entries).
        """
        stmt = select(
            UserApiKey.provider,
            UserApiKey.encrypted_key,
            UserApiKey.model_selection,
            UserApiKey.updated_at,
        ).where(UserApiKey.user_id == user_id)

        saved_stmt = (
            select(UserSavedModel.provider, UserSavedModel.model)
            .where(UserSavedModel.user_id == user_id)
            .order_by(UserSavedModel.created_at, UserSavedModel.id)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.fetchall()

            saved_map: Dict[str, list] = {}
            for saved_row in (await s.execute(saved_stmt)).fetchall():
                saved_map.setdefault(saved_row[0], []).append(saved_row[1])

            result_map: Dict[str, Dict[str, Any]] = {}

            for row in rows:
                provider = row[0]
                if kind and provider_kind(provider) != kind:
                    continue
                decrypted = await _decrypt_and_upgrade(s, user_id, provider, row[1])
                masked = mask_api_key(decrypted) if decrypted else None

                active_model = row[2]
                models = saved_map.get(provider, [])
                if active_model and active_model not in models:
                    # migration 前寫入的舊列：使用中的模型必須出現在清單裡
                    models = [active_model] + models

                result_map[provider] = {
                    "has_key": bool(decrypted),
                    "masked_key": masked,
                    "model": active_model,
                    "models": models,
                    "updated_at": row[3].isoformat() if row[3] else None,
                    "corrupted": not bool(decrypted),
                }

            # Ensure all supported providers (of the requested kind) have an entry
            if kind == "llm":
                expected = LLM_PROVIDERS
            elif kind == "tool":
                expected = TOOL_PROVIDERS
            else:
                expected = SUPPORTED_PROVIDERS
            for provider in expected:
                pb = platform_binding(provider)
                if pb:
                    # 平台模型：每個人預設就綁著（2026-09-22 DANNY：Agent 配置、模型切換都要選得到）
                    result_map[provider] = {
                        "has_key": True,
                        "masked_key": None,
                        "model": pb["model"],
                        "models": [pb["model"]],
                        "updated_at": None,
                        "platform": True,
                    }
                elif provider not in result_map:
                    result_map[provider] = {
                        "has_key": False,
                        "masked_key": None,
                        "model": None,
                        "models": [],
                        "updated_at": None,
                    }

            return result_map

    async def delete_user_api_key(
        self,
        user_id: str,
        provider: str,
        session: AsyncSession | None = None,
    ) -> Dict[str, Any]:
        """Delete a user's API key for a specific provider (含保存模型清單)."""
        stmt = delete(UserApiKey).where(
            UserApiKey.user_id == user_id,
            UserApiKey.provider == provider,
        )

        async with using_session(session) as s:
            await s.execute(stmt)
            await s.execute(
                delete(UserSavedModel).where(
                    UserSavedModel.user_id == user_id,
                    UserSavedModel.provider == provider,
                )
            )

        return {"success": True}

    async def delete_all_user_api_keys(
        self,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> Dict[str, Any]:
        """Delete all API keys for a user (used on account deletion)."""
        stmt = delete(UserApiKey).where(UserApiKey.user_id == user_id)

        async with using_session(session) as s:
            await s.execute(stmt)
            await s.execute(
                delete(UserSavedModel).where(UserSavedModel.user_id == user_id)
            )

        return {"success": True}

    async def save_user_model_selection(
        self,
        user_id: str,
        provider: str,
        model: str,
        session: AsyncSession | None = None,
    ) -> Dict[str, Any]:
        """Save user's model selection without changing the API key."""
        model_name = (model or "").strip()
        if not model_name:
            return {"success": False, "error": "Model cannot be empty"}

        now = datetime.now(timezone.utc)
        stmt = (
            update(UserApiKey)
            .where(
                UserApiKey.user_id == user_id,
                UserApiKey.provider == provider,
            )
            .values(model_selection=model_name, updated_at=now)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            if result.rowcount == 0:
                return {"success": False, "error": "No API key found for this provider"}
            # invariant：使用中的模型必在保存清單內（切換到清單外的模型時補記）
            await s.execute(_saved_model_insert(user_id, provider, model_name))

        return {"success": True}


user_api_keys_repo = UserApiKeysRepository()
