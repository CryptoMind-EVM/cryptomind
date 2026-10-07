"""
Tool API Key Resolver (BYOK — Bring Your Own Key)

工具在執行時透過此模組解析要用哪把金鑰：

    使用者自帶金鑰 (user_api_keys, key_kind='tool')
      → 沒有則 fallback 官方 env 金鑰（僅限你願意補貼的工具）
      → 都沒有 → 回傳 None（呼叫端應停用該工具並提示使用者設定金鑰）

由於 @tool 函式是同步執行（在 async agent 內），這裡刻意使用同步 DB 連線
(core.database.connection) 讀取並解密，避免在同步上下文呼叫 async ORM。

當前使用者透過 contextvar 注入：agent 執行前呼叫 set_current_user_id()。
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextvars import ContextVar
from typing import Optional

from utils.encryption import decrypt_api_key

logger = logging.getLogger(__name__)

# agent 執行期間注入的當前使用者 id；工具讀此值去查自己的金鑰
_current_user_id: ContextVar[Optional[str]] = ContextVar(
    "current_user_id", default=None
)

# Mixer Step 4（記憶分層）：當前執行身分的 agent id（preset 單一 profile；
# 無法判定＝None＝共享視角）。與 user_id 同機制——工具層（remember 刪除
# pre-check、list_my_skills_memory）以此讀到「agent 視角」的記憶範圍。
_current_agent_id: ContextVar[Optional[str]] = ContextVar(
    "current_agent_id", default=None
)


def set_current_user_id(user_id: Optional[str]):
    """設定當前執行緒/任務的使用者 id，回傳 token 供 reset 用。"""
    return _current_user_id.set(user_id)


def reset_current_user_id(token) -> None:
    """還原 contextvar（搭配 set_current_user_id 的 token）。"""
    try:
        _current_user_id.reset(token)
    except (ValueError, LookupError):
        pass


def get_current_user_id() -> Optional[str]:
    return _current_user_id.get()


def set_current_agent_id(agent_id: Optional[str]):
    """設定當前執行身分的 agent id（Mixer Step 4），回傳 token 供 reset 用。"""
    return _current_agent_id.set(agent_id)


def reset_current_agent_id(token) -> None:
    try:
        _current_agent_id.reset(token)
    except (ValueError, LookupError):
        pass


def get_current_agent_id() -> Optional[str]:
    return _current_agent_id.get()


def _get_user_tool_key(user_id: str, provider: str) -> Optional[str]:
    """同步讀取並解密某使用者的工具金鑰，查無回 None。"""
    from core.database.connection import get_connection

    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            "SELECT encrypted_key FROM user_api_keys WHERE user_id = %s AND provider = %s",
            (user_id, provider),
        )
        row = c.fetchone()
        if not row or not row[0]:
            return None
        decrypted = decrypt_api_key(row[0])
        return decrypted or None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("[key_resolver] failed reading %s key: %s", provider, e)
        return None
    finally:
        conn.close()


def resolve_tool_key(
    provider: str,
    official_env: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """
    解析工具要用的金鑰。

    Args:
        provider: 工具供應商代號（如 "tavily"）
        official_env: 官方 fallback 用的環境變數名稱；None 表示不提供官方 fallback
                      （即強制 BYOK）
        user_id: 指定使用者；省略時自 contextvar 取當前使用者

    Returns:
        可用的金鑰字串，或 None（呼叫端應停用工具並提示設定金鑰）
    """
    uid = user_id or get_current_user_id()

    if uid:
        user_key = _get_user_tool_key(uid, provider)
        if user_key:
            return user_key

    if official_env:
        official = os.getenv(official_env)
        if official:
            return official

    return None


def has_tool_key(provider: str, user_id: Optional[str] = None) -> bool:
    """使用者是否已設定該工具的金鑰（不含官方 fallback）。"""
    uid = user_id or get_current_user_id()
    if not uid:
        return False
    return bool(_get_user_tool_key(uid, provider))
