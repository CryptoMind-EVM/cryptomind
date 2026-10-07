"""Agent timeout 測試 — claw_loop._run_agent。

2026-08-02 改：移除 idle timeout（誤殺 reasoning model），改用 recursion_limit。
保留的測試：
- watchdog 邏輯（通用 async 模式，不依賴模組常數）
- AGENT_EXECUTION_TIMEOUT env 覆寫（寬鬆 safety net）
"""

import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_run_agent_idle_timeout_triggers():
    """活動停止超過 idle 上限 → TimeoutError。

    模擬 execute_streaming 卡住不發 token/tool，watchdog 應在 idle timeout 後觸發。
    """
    import time

    IDLE_LIMIT = 0.3

    async def _stuck_execute():
        # 模擬卡住——永遠不完成、不觸發 callback
        await asyncio.sleep(100)

    async def _test():
        exec_task = asyncio.ensure_future(_stuck_execute())
        last_activity = [time.monotonic()]

        async def _watchdog():
            while not exec_task.done():
                await asyncio.sleep(0.1)
                now = time.monotonic()
                if now - last_activity[0] > IDLE_LIMIT:
                    raise asyncio.TimeoutError()

        done, pending = await asyncio.wait(
            [exec_task, asyncio.ensure_future(_watchdog())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        # watchdog 應先觸發（idle 過久）
        timeout_triggered = any(
            d.exception() is not None for d in done if not d.cancelled()
        )
        assert timeout_triggered
        exec_task.cancel()

    asyncio.new_event_loop().run_until_complete(_test())


@pytest.mark.unit
def test_run_agent_activity_resets_idle():
    """持續有活動（token/tool）→ idle clock 重置 → 不觸發 timeout。"""
    import time

    IDLE_LIMIT = 0.3

    async def _active_execute():
        # 模擬持續發 token，每 0.1s 一次，共 0.6s（超過 idle 0.3 但持續活動）
        for _ in range(6):
            await asyncio.sleep(0.1)
        return MagicMock()

    async def _test():
        exec_task = asyncio.ensure_future(_active_execute())
        last_activity = [time.monotonic()]

        async def _watchdog():
            while not exec_task.done():
                await asyncio.sleep(0.05)
                now = time.monotonic()
                if now - last_activity[0] > IDLE_LIMIT:
                    raise asyncio.TimeoutError()
                # 模擬 callback 每 0.1s 重置活動時間
                last_activity[0] = now

        done, _pending = await asyncio.wait(
            [exec_task, asyncio.ensure_future(_watchdog())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        # exec 應正常完成（持續活動，watchdog 沒觸發）
        assert exec_task in done
        assert exec_task.exception() is None

    asyncio.new_event_loop().run_until_complete(_test())


@pytest.mark.unit
def test_execution_timeout_env_override(monkeypatch):
    """AGENT_EXECUTION_TIMEOUT 預設 1800（寬鬆 safety net），可 env 覆寫。"""
    monkeypatch.setenv("AGENT_EXECUTION_TIMEOUT", "900")
    import importlib

    import core.agents.manager._main as _main

    importlib.reload(_main)
    assert _main.AGENT_EXECUTION_TIMEOUT == 900
    importlib.reload(_main)
