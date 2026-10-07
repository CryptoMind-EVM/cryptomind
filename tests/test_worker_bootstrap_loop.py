"""回歸測試：worker bootstrap 的 asyncio.run 衝突（PR 修復）。

根因（線上 run e040b402 觸發）：
  analysis_worker.py 的 _run_job 是 async（在 asyncio.run(_run_job) 內跑），
  卻直接同步呼叫 bootstrap() → load_mcp_tools_sync() → asyncio.run()。
  asyncio.run() 在已 running loop 內會炸：
    RuntimeError: asyncio.run() cannot be called from a running event loop
  導致 MCP tools 全部載入失敗（fail-soft），LLM 工具池殘缺。

修法：bootstrap 用 run_sync 丟到 thread（thread 內無 running loop，asyncio.run 合法），
      對齊 api/routers/analysis.py:_do_bootstrap。

本測試驗證：load_mcp_tools_sync 經 run_sync（thread）呼叫不炸；
直接在 running loop 內呼叫會炸（重現 bug 條件）。
"""

from __future__ import annotations

import asyncio
import warnings

import pytest

from api.utils import run_sync

# bug 條件下 asyncio.run 在 RuntimeError 前會建出未 await 的 coroutine，
# 噴 RuntimeWarning——那是測試正在證明的行為，過濾掉避免噪音。
warnings.filterwarnings("ignore", message="coroutine.*was never awaited")


def _calls_asyncio_run() -> None:
    """模擬 bootstrap 內部 load_mcp_tools_sync 的 asyncio.run 呼叫。

    MCP_ENABLED=0 時 load_mcp_tools_sync 直接 return []，不會碰 asyncio.run。
    所以這裡直接測 asyncio.run 本身的行為——這才是 bug 的核心機制。
    """
    # 跑一個空的 async main（不回傳 coroutine，避免無謂 warning）。
    asyncio.run(_noop_async())


async def _noop_async() -> None:
    """空的 async 函式，僅滿足 asyncio.run 需要 coroutine 的簽章。"""
    return None


class TestAsyncioRunInRunningLoop:
    """證明 bug 條件與修復機制。"""

    def test_asyncio_run_in_running_loop_raises(self):
        """重現 bug：asyncio.run() 在 running loop 內呼叫會炸。"""

        async def in_running_loop():
            with pytest.raises(RuntimeError, match="asyncio.run.*running event loop"):
                _calls_asyncio_run()

        asyncio.run(in_running_loop())

    def test_asyncio_run_via_run_sync_works(self):
        """修復機制：run_sync 丟到 thread，thread 內 asyncio.run 合法。"""

        async def driver():
            # run_sync 把 _calls_asyncio_run 丟到 thread executor，
            # thread 內沒有 running loop → asyncio.run() 正常。
            await run_sync(_calls_asyncio_run)

        asyncio.run(driver())  # 不 raise 即通過


class TestMcpLoaderDisabledSafePath:
    """MCP_ENABLED=0 時 load_mcp_tools_sync 不碰 asyncio.run（安全路徑）。"""

    def test_disabled_returns_empty_without_loop(self, monkeypatch):
        monkeypatch.setenv("MCP_ENABLED", "0")
        # 重新 import 確保 env 生效（_is_enabled 在呼叫時讀 env）
        from core.tools.mcp_loader import load_mcp_tools_sync

        result = load_mcp_tools_sync()
        assert result == []
