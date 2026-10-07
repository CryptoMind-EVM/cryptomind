"""WebSocket 共用安全工具 — 背景 token 重驗(GAP-3 根治)。

職責
====
WS endpoint 在握手時驗證一次 token,之後進入無限迴圈。若 token 過期或被 revoke,
長連線不會被切斷 → 權限不自動收回。本模組提供背景重驗 watcher:定期檢查 token
有效性,失效就主動 close(4401)。

設計取捨(只改 server,不碰前端):
- 不要求前端心跳夾帶 token(那要改 client 多處,風險中)
- server 端每 REAUTH_INTERVAL 秒重驗;revoke/過期後最多一個區間生效
- 前端收到 close 後依既有邏輯重連(重連時重新握手 + 取新 token)

對應 access token 24h 過期 + revoke(GAP-2):即使 GAP-2 multi-worker revoke
不同步,本 watcher 至少能在「token 自然過期」時收回長連線權限。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# 重驗區間(秒)。access token 24h 過期,15 分鐘重驗一次足夠及時且不耗資源。
REAUTH_INTERVAL_SECONDS = 15 * 60

# WS close code:4401 = auth expired(自訂,4xxx 範圍給應用層)
WS_CLOSE_AUTH_EXPIRED = 4401


def _is_test_token(token: str) -> bool:
    """TEST_MODE 下的 test- 開頭 token 跳過 JWT 重驗。"""
    return (
        os.getenv("TEST_MODE", "").lower() == "true"
        and isinstance(token, str)
        and token.startswith("test-")
    )


async def _verify_token_still_valid(token: str) -> bool:
    """token 是否仍有效(過期 / revoke / 簽章失效 → False)。

    複用 api.deps.verify_token(它檢查 exp + 簽章)。
    """
    if not token:
        return False
    if _is_test_token(token):
        return True  # test mode 不過期
    try:
        from api.deps import is_token_revoked, verify_token

        verify_token(token)
        # revoke 檢查:token 可能簽章有效但已被登出 revoke
        token_hash = token  # is_token_revoked 內部會 hash
        if is_token_revoked(token_hash):
            return False
        return True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return False


async def start_reauth_watcher(
    websocket: WebSocket,
    token: Optional[str],
    interval: int = REAUTH_INTERVAL_SECONDS,
) -> asyncio.Task:
    """啟動背景 token 重驗 task。回傳 task 供呼叫端在 finally 取消。

    用法:
        watcher = await start_reauth_watcher(websocket, token)
        try:
            while True:
                await websocket.receive_text()
                ...
        finally:
            watcher.cancel()
    """

    async def _watch():
        while True:
            await asyncio.sleep(interval)
            if not await _verify_token_still_valid(token):
                logger.info(
                    "[WS-Reauth] token expired/revoked during connection, closing"
                )
                try:
                    await websocket.close(
                        code=WS_CLOSE_AUTH_EXPIRED, reason="Token expired"
                    )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    pass  # 可能已斷線
                return

    return asyncio.create_task(_watch())
