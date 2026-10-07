"""Tests for 方向②: Hermes-style memory curation.

Covers:
- MEMORY_CHAR_BUDGET read-time truncation (_apply_memory_budget)
- read_facts touch (access_count increment, last_accessed update)
- prune_stale_facts (delete old + never-accessed facts)
- reconcile_user_facts_columns (self-healing schema)
"""

from __future__ import annotations

from unittest.mock import patch

from core.database import memory as memory_mod
from core.database.memory import (
    MEMORY_CHAR_BUDGET,
    MemoryStore,
    _apply_memory_budget,
)

# ============================================================================
# _apply_memory_budget — 讀時預算截斷
# ============================================================================


class TestApplyMemoryBudget:
    def test_short_memory_unchanged(self):
        """短記憶不截斷。"""
        assert _apply_memory_budget("短記憶", 4000) == "短記憶"

    def test_empty_context_returns_empty(self):
        assert _apply_memory_budget("", 4000) == ""

    def test_within_tolerance_not_truncated(self):
        """在容差範圍內(budget * 1.1)不截斷,避免邊界抖動。"""
        text = "x" * int(MEMORY_CHAR_BUDGET * 1.05)  # 5% 超標
        assert _apply_memory_budget(text, MEMORY_CHAR_BUDGET) == text

    def test_over_budget_truncates_and_annotates(self):
        """明顯超標時截斷並標註。"""
        text = "重要記憶行\n" * 1000  # 遠超 budget
        result = _apply_memory_budget(text, MEMORY_CHAR_BUDGET)
        assert len(result) < len(text)
        # 標註文案已英文化（#352）——斷言英文關鍵字，避免與翻譯耦合。
        assert "omitted" in result.lower()
        # 截斷後長度應在 budget 附近(加註解文字)
        assert len(result) < MEMORY_CHAR_BUDGET + 100

    def test_truncates_at_line_boundary(self):
        """截斷應在行邊界切,不切斷行中間。"""
        text = "完整行1\n完整行2\n" * 500
        result = _apply_memory_budget(text, 4000)
        # 結尾不應是行中間的字(應是 \n 或註解)
        assert result.endswith("\n") or "omitted" in result.lower()


# ============================================================================
# read_facts touch — 存取計數
# ============================================================================


class TestReadFactsTouch:
    def test_read_facts_increments_access_count(self):
        """read_facts 應更新 last_accessed/access_count(touch)。"""
        store = MemoryStore("u1", "s1")
        with (
            patch.object(memory_mod, "DatabaseBase") as mock_db,
        ):
            mock_db.query_all.return_value = [
                {"key": "pref", "value": "BTC", "confidence": "high", "source_turn": 1}
            ]
            mock_db.execute.return_value = None
            facts = store.read_facts()

        assert "pref" in facts
        # 應有 touch 的 execute 呼叫(UPDATE access_count)
        execute_calls = mock_db.execute.call_args_list
        assert any(
            "access_count = access_count + 1" in str(c) for c in execute_calls
        ), "read_facts 應 touch access_count"

    def test_read_facts_touch_failure_silent(self):
        """touch 失敗時靜默,不擋讀取。"""
        store = MemoryStore("u1", "s1")
        with patch.object(memory_mod, "DatabaseBase") as mock_db:
            mock_db.query_all.return_value = [
                {"key": "k", "value": "v", "confidence": "high", "source_turn": 1}
            ]
            # touch 的 execute 拋例外
            mock_db.execute.side_effect = RuntimeError("db down")
            facts = store.read_facts()  # 不應拋

        assert "k" in facts  # 讀取仍成功

    def test_read_facts_no_touch_when_empty(self):
        """無事實時不 touch(無意義)。"""
        store = MemoryStore("u1", "s1")
        with patch.object(memory_mod, "DatabaseBase") as mock_db:
            mock_db.query_all.return_value = []
            store.read_facts()

        mock_db.execute.assert_not_called()


# ============================================================================
# prune_stale_facts — 過時事實淘汰
# ============================================================================


class TestPruneStaleFacts:
    def test_prune_executes_delete_query(self):
        """prune_stale_facts 應執行 DELETE 並失效 cache。"""
        store = MemoryStore("u1", "s1")
        with (
            patch.object(memory_mod, "DatabaseBase") as mock_db,
            patch.object(memory_mod, "_invalidate_memory_cache") as mock_inval,
        ):
            mock_db.execute.return_value = None
            store.prune_stale_facts(max_age_days=30)

        # 應有 DELETE 語句
        execute_call = mock_db.execute.call_args
        assert "DELETE FROM user_facts" in str(execute_call)
        assert "access_count = 0" in str(execute_call)
        mock_inval.assert_called_once_with("u1")

    def test_prune_silent_on_db_error(self):
        """DB 失敗時靜默回 0。"""
        store = MemoryStore("u1", "s1")
        with patch.object(memory_mod, "DatabaseBase") as mock_db:
            mock_db.execute.side_effect = RuntimeError("db down")
            result = store.prune_stale_facts()

        assert result == 0  # 不拋,回 0


# ============================================================================
# reconcile_user_facts_columns — schema 自癒
# ============================================================================


class TestReconcileUserFactsColumns:
    def test_reconcile_adds_three_columns(self):
        """reconcile 應補 created_at/last_accessed/access_count 三欄。"""
        from core.database.schema import reconcile_user_facts_columns

        executed = []

        class _FakeCursor:
            def execute(self, sql, *args):
                executed.append(sql)

        # 模擬 _run_reconcile_steps 會檢查欄位存在性 — 直接驅動 cursor
        # (reconcile 透過 information_schema 檢查,這裡只驗 SQL 被送出)
        with patch(
            "core.database.schema._run_reconcile_steps"
        ) as mock_reconcile:
            reconcile_user_facts_columns(_FakeCursor())
            # _run_reconcile_steps 應被呼叫,帶 3 個 step
            call_args = mock_reconcile.call_args
            steps = call_args[0][2]  # 第三個位置參數 = steps list
            column_names = [s[0] for s in steps]
            assert "created_at" in column_names
            assert "last_accessed" in column_names
            assert "access_count" in column_names
