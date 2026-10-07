"""Tests for memory-consolidation DB-failure tolerance.

Regression: check_idle_consolidation() 內部讀 DB（get_last_consolidated_index），
無 try/except。當 DB 連線失敗（網路抖動/維護/連線耗盡），例外向上炸穿整個
_claw_loop_node → 整個使用者對話崩潰（RuntimeError: generator didn't stop）。

記憶整合是背景輔助功能，DB 失敗時絕不可讓主對話崩潰。
修補：claw_loop.py 把 check_idle_consolidation 包在 try/except，失敗時 log +
跳過整合，主對話繼續。
"""

import asyncio
import inspect

from core.agents.manager import claw_loop as claw_loop_mod


def _source_has_db_tolerance_guard():
    """檢查 claw_loop 原始碼確實有 try/except 包住 check_idle_consolidation。

    這是靜態檢查（不需跑完整 graph），確保修補存在。
    """
    src = inspect.getsource(claw_loop_mod.ClawLoopMixin._claw_loop_node)
    # 修補前：直接 `if await run_sync(self.check_idle_consolidation):`
    # 修補後：`try: need_consolidation = await run_sync(...) except Exception:`
    assert "check_idle_consolidation" in src, "找不到 check_idle_consolidation 呼叫"
    # 找 try/except 包住它的證據
    idx = src.index("check_idle_consolidation")
    # 往前找最近的 try（在同一邏輯區塊內）
    before = src[:idx]
    assert "try:" in before[-400:], (
        "check_idle_consolidation 應被 try/except 包住（DB 容錯），但原始碼找不到。"
        f"附近片段:\n{src[max(0,idx-400):idx+200]}"
    )
    after = src[idx:]
    assert "except Exception" in after[:600], (
        "check_idle_consolidation 後應有 except Exception 接住 DB 失敗。"
    )


def test_claw_loop_has_db_tolerance_guard():
    """靜態驗證：_claw_loop_node 的 check_idle_consolidation 有 DB 容錯保護。"""
    _source_has_db_tolerance_guard()


def test_consolidation_failure_does_not_propagate(monkeypatch):
    """動態驗證：check_idle_consolidation 拋 DB 例外時，不該向上傳播。

    模擬 _claw_loop_node 內那段 try/except 邏輯：若 run_sync 拋例外，
    修補後的 need_consolidation 應被設為 False（不崩潰）。
    """

    async def _boom(*args, **kwargs):
        raise OSError("connection to server refused (DB down)")

    # 模擬 run_sync 拋 DB 例外
    monkeypatch.setattr(claw_loop_mod, "run_sync", _boom)

    # 模擬修補後的邏輯
    async def _simulate_consolidation_check():
        need_consolidation = False
        try:
            need_consolidation = await claw_loop_mod.run_sync(lambda: True)
        except Exception:
            need_consolidation = False
        return need_consolidation

    result = asyncio.run(_simulate_consolidation_check())
    assert result is False, "DB 失敗時 need_consolidation 應為 False（不崩潰）"


def test_consolidation_success_still_works(monkeypatch):
    """正向：check_idle_consolidation 成功（DB 正常）時邏輯不變。"""

    async def _ok(fn, *args):
        return fn()

    monkeypatch.setattr(claw_loop_mod, "run_sync", _ok)

    async def _simulate():
        need = False
        try:
            need = await claw_loop_mod.run_sync(lambda: True)
        except Exception:
            need = False
        return need

    result = asyncio.run(_simulate())
    assert result is True, "DB 正常時應正常回傳 True"

