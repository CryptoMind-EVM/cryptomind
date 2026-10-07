"""工具步驟記帳：每個 finish 要打到自己 start 的那一列（2026-09-06）。

症狀：進度卡上的工具列一直轉圈，不會變 ✓。

2026-08-25 修過一次（並行 tool_calls 用「當前計數」當 step，所有 finish 都
打到最後一列），改成具名記帳。但那版是 ``_by_name[name] = count`` 單值覆寫，
遇到**同名工具被呼叫兩次**就再次失效：

- fan-out 時 _tool_steps 跨節點共用，多個 agent 都會呼叫 get_crypto_price
- 同一個 agent 內同名工具連續呼叫（BTC 一次、ETH 一次）

兩種情況下較早那一列的 step 號被覆寫，finish 永遠打不到它。
"""

from __future__ import annotations

import pytest

from core.agents.manager.claw_loop import _ToolStepTracker

pytestmark = pytest.mark.unit


class TestEachFinishHitsItsOwnRow:
    def test_distinct_tools(self):
        t = _ToolStepTracker()
        a, b = t.start("price"), t.start("news")
        assert (a, b) == (1, 2)
        assert t.end("price") == 1
        assert t.end("news") == 2

    def test_same_tool_twice_does_not_collide(self):
        """fan-out：兩個節點都叫 get_crypto_price。舊版兩個 end 都回 2。"""
        t = _ToolStepTracker()
        first, second = t.start("price"), t.start("price")
        assert (first, second) == (1, 2)
        assert t.end("price") == 1, "先完成的要打回第 1 列，不是最後一列"
        assert t.end("price") == 2

    def test_interleaved_across_fanout_nodes(self):
        """交錯：A.price → B.price → B.news → A 完成 → B 完成。"""
        t = _ToolStepTracker()
        t.start("price")   # 1 (node A)
        t.start("price")   # 2 (node B)
        t.start("news")    # 3 (node B)
        assert t.end("price") == 1
        assert t.end("news") == 3
        assert t.end("price") == 2

    def test_unmatched_finish_degrades_to_latest(self):
        """沒有對應 start 的 finish（理論異常）不得炸，退回當前計數。"""
        t = _ToolStepTracker()
        t.start("price")
        assert t.end("never_started") == 1


class TestBookkeepingSurface:
    def test_names_keeps_insertion_order_without_duplicates(self):
        """loop_fork 卡片要列「實際呼叫過的工具」——重複呼叫不該重複列出。"""
        t = _ToolStepTracker()
        for n in ("price", "news", "price"):
            t.start(n)
        assert t.names() == ["price", "news"]

    def test_total_counts_every_call(self):
        t = _ToolStepTracker()
        for n in ("price", "news", "price"):
            t.start(n)
        assert t.total == 3, "total 給 run metrics 用，重複呼叫要各算一次"
