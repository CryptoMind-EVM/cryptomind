"""WebSocket 背景 token 重驗測試 — api/routers/ws_auth.py(GAP-3)。

依 AGENTS.md「success / reject / edge」:
- success:有效 token 不被誤斷
- reject:過期/revoke token → watcher 觸發 close(4401)
- edge:test token、空 token、close 已斷線不拋

背景:WS 握手後進入無限迴圈,token 過期不會自動斷。watcher 定期重驗,
失效就 close,收回長連線權限。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routers.ws_auth import (
    REAUTH_INTERVAL_SECONDS,
    WS_CLOSE_AUTH_EXPIRED,
    _verify_token_still_valid,
    start_reauth_watcher,
)

# ──────────────────────────────────────────────────────────────────────────────
# _verify_token_still_valid
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_token_still_valid():
    """有效 token → True。"""
    with patch("api.deps.verify_token", return_value={"sub": "u1"}), patch(
        "api.deps.is_token_revoked", return_value=False
    ):
        assert await _verify_token_still_valid("good-token") is True


@pytest.mark.asyncio
async def test_expired_token_invalid():
    """過期 token(verify_token 拋錯)→ False。"""
    with patch("api.deps.verify_token", side_effect=Exception("expired")):
        assert await _verify_token_still_valid("expired-token") is False


@pytest.mark.asyncio
async def test_revoked_token_invalid():
    """被 revoke 的 token(簽章有效但 revoked)→ False。"""
    with patch("api.deps.verify_token", return_value={"sub": "u1"}), patch(
        "api.deps.is_token_revoked", return_value=True
    ):
        assert await _verify_token_still_valid("revoked-token") is False


@pytest.mark.asyncio
async def test_empty_token_invalid():
    """空 token → False。"""
    assert await _verify_token_still_valid("") is False
    assert await _verify_token_still_valid(None) is False


@pytest.mark.asyncio
async def test_test_mode_token_valid(monkeypatch):
    """TEST_MODE 下 test- 開頭 token 不過期。"""
    monkeypatch.setenv("TEST_MODE", "true")
    assert await _verify_token_still_valid("test-user-123") is True


# ──────────────────────────────────────────────────────────────────────────────
# start_reauth_watcher — close 行為
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watcher_closes_on_expired_token():
    """token 失效時 watcher 應 close(4401)。"""
    ws = MagicMock()
    ws.close = AsyncMock()
    # 用極短 interval 加速測試
    with patch(
        "api.routers.ws_auth._verify_token_still_valid", return_value=False
    ):
        task = await start_reauth_watcher(ws, "expired", interval=0.05)
        # 等 watcher 跑一輪
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    ws.close.assert_called_once()
    call_kwargs = ws.close.call_args
    assert call_kwargs.kwargs.get("code") == WS_CLOSE_AUTH_EXPIRED


@pytest.mark.asyncio
async def test_watcher_keeps_valid_token_alive():
    """token 有效時 watcher 不該 close。"""
    ws = MagicMock()
    ws.close = AsyncMock()
    with patch(
        "api.routers.ws_auth._verify_token_still_valid", return_value=True
    ):
        task = await start_reauth_watcher(ws, "good", interval=0.05)
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    ws.close.assert_not_called()


@pytest.mark.asyncio
async def test_watcher_close_already_disconnected_no_raise():
    """close 時 WS 已斷(拋例外)→ 不該傳播(watcher 安靜結束)。"""
    ws = MagicMock()
    ws.close = AsyncMock(side_effect=RuntimeError("already closed"))
    with patch(
        "api.routers.ws_auth._verify_token_still_valid", return_value=False
    ):
        task = await start_reauth_watcher(ws, "expired", interval=0.05)
        await asyncio.sleep(0.15)
        # watcher 應已安靜 return(不拋)
        assert task.done()
        # 不該有未處理例外
        try:
            await task
        except RuntimeError:
            pytest.fail("watcher 不該傳播 close 例外")


@pytest.mark.asyncio
async def test_watcher_cancellable():
    """watcher task 可被 cancel(連線正常斷開時)。"""
    ws = MagicMock()
    ws.close = AsyncMock()
    with patch(
        "api.routers.ws_auth._verify_token_still_valid", return_value=True
    ):
        task = await start_reauth_watcher(ws, "good", interval=0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    ws.close.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# 常數
# ──────────────────────────────────────────────────────────────────────────────


def test_reauth_interval_reasonable():
    """重驗區間該在合理範圍(不要太頻繁耗資源,不要太久才生效)。"""
    assert 60 <= REAUTH_INTERVAL_SECONDS <= 60 * 60  # 1 分鐘 ~ 1 小時


def test_close_code_is_4401():
    """自訂 close code 在應用層範圍(4xxx)。"""
    assert WS_CLOSE_AUTH_EXPIRED == 4401
