"""TraceCollector query 同步測試 — core/agents/tracing.py。

驗證 PR A 的修法：trace summary 的 query 不再永遠空字串。
根因是 _main.py 建 TraceCollector 沒傳 query（query 走 LangGraph state 通道，
物件層讀不到）。修法是在 _wrap_with_trace 把 state.query 寫回 collector.query。

這裡測 TraceCollector.query 屬性的行為契約（被設值後 summary 能反映）。
"""

from __future__ import annotations

from core.agents.tracing import TraceCollector


def test_query_defaults_empty():
    """沒傳 query 時預設空字串（重現修法前的狀況）。"""
    tc = TraceCollector(session_id="s1")
    assert tc.query == ""


def test_query_set_via_constructor():
    """建構時可傳 query。"""
    tc = TraceCollector(session_id="s1", query="比特幣多少")
    assert tc.query == "比特幣多少"


def test_query_assignable_after_construction():
    """建構後可外部設 query（這是 _wrap_with_trace 修法所依賴的行為）。

    _main.py 的 _wrap_with_trace 在節點邊界做：
        if state_query and not self._trace_collector.query:
            self._trace_collector.query = state_query
    確認這個賦值有效，且後續 summary 能反映。
    """
    tc = TraceCollector(session_id="s1")  # 初始 query=""
    assert tc.query == ""

    # 模擬 _wrap_with_trace 的銜接邏輯
    state_query = "以太坊現在價格"
    if state_query and not tc.query:
        tc.query = state_query

    assert tc.query == "以太坊現在價格"


def test_query_not_overwritten_if_already_set():
    """query 已有值時不應被覆蓋（_wrap_with_trace 的 not self.query 條件）。"""
    tc = TraceCollector(session_id="s1", query="原始問題")
    state_query = "另一個問題"

    if state_query and not tc.query:
        tc.query = state_query  # 不該執行（tc.query 已有值）

    assert tc.query == "原始問題"  # 保持原值


def test_summary_reflects_query():
    """trace log 應包含 query（這是修法要解決的：log 不再 query=""）。"""
    tc = TraceCollector(session_id="s1", query="BTC price")
    log = tc.format_trace_log()
    # log 應含 query 內容
    assert "BTC price" in log
