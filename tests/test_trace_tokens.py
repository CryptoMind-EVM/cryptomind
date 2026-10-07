"""Token 計數橋接測試 — TokenTracker → TraceCollector。

驗證修復 ``Tokens: 0`` bug 的三個環節：
1. ``_extract_token_usage`` 相容 LangChain（input/output）與傳統（prompt/completion）金鑰命名。
2. ``TokenTracker.usage_since`` 正確計算本次 graph 執行的 token 增量（扣除歷史）。
3. ``TraceCollector`` 把 token_usage 正確累加進 summary / log。

根因：``_wrap_with_trace`` 呼叫 ``finish_trace`` 時沒傳 ``token_usage``，
加上 tracing.py 金鑰命名不一致（讀 prompt_tokens 但上游可能給 input_tokens），
導致 ``Tokens: 0``。
"""

from __future__ import annotations

from core.agents.token_tracker import TokenTracker, TokenUsage
from core.agents.tracing import TraceCollector, _extract_token_usage

# ── _extract_token_usage：雙金鑰相容 ───────────────────────────────────────────


def test_extract_handles_langchain_keys():
    """LangChain usage_metadata 用 input_tokens/output_tokens。"""
    out = _extract_token_usage(
        {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    )
    assert out == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


def test_extract_handles_traditional_keys():
    """TokenTracker / OpenAI 傳統格式用 prompt_tokens/completion_tokens。"""
    out = _extract_token_usage(
        {"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280}
    )
    assert out == {"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280}


def test_extract_none_returns_zeros():
    """沒有 token_usage 時回全零（不該讓 summary 報錯）。"""
    assert _extract_token_usage(None) == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_extract_infers_total_when_missing():
    """total_tokens 缺漏時由 prompt+completion 推導。"""
    out = _extract_token_usage({"input_tokens": 30, "output_tokens": 20})
    assert out["total_tokens"] == 50


# ── TokenTracker.usage_since：本次增量 ─────────────────────────────────────────


def test_usage_since_returns_only_increment():
    """usage_since 只回傳 baseline 之後新增的量，不含歷史。"""
    tracker = TokenTracker()
    # 模擬歷史（前一個請求）
    tracker.record(TokenUsage("m", 1000, 500, 1500))
    baseline = tracker.total_requests()

    # 本次新增
    tracker.record(TokenUsage("m", 200, 100, 300))
    tracker.record(TokenUsage("m", 50, 25, 75))

    inc = tracker.usage_since(baseline)
    assert inc["prompt_tokens"] == 250
    assert inc["completion_tokens"] == 125
    assert inc["total_tokens"] == 375


def test_usage_since_zero_when_no_new_usage():
    """node 內沒有 LLM 呼叫時，增量應為全零（不影響 Tokens 顯示）。"""
    tracker = TokenTracker()
    tracker.record(TokenUsage("m", 100, 50, 150))
    baseline = tracker.total_requests()

    inc = tracker.usage_since(baseline)
    assert inc == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# ── TraceCollector：token 累加進 summary / log ─────────────────────────────────


def test_trace_collector_accumulates_tokens_in_summary():
    """finish_trace 帶 token_usage 時，summary 應反映累加總量。"""
    tc = TraceCollector(session_id="s1", query="test")
    t1 = tc.start_trace("node_a")
    tc.finish_trace(t1, token_usage={"prompt_tokens": 100, "completion_tokens": 40})
    t2 = tc.start_trace("node_b")
    tc.finish_trace(t2, token_usage={"input_tokens": 60, "output_tokens": 30})

    summary = tc.get_trace_summary()
    tokens = summary["total_tokens"]
    assert tokens["prompt"] == 160  # 100 + 60（跨金鑰命名）
    assert tokens["completion"] == 70  # 40 + 30
    assert tokens["total"] == 230


def test_trace_log_shows_nonzero_tokens():
    """format_trace_log 的 'Tokens:' 行應顯示實際數字，不再恆為 0。"""
    tc = TraceCollector(session_id="s1", query="test")
    t = tc.start_trace("claw_loop")
    tc.finish_trace(t, token_usage={"prompt_tokens": 500, "completion_tokens": 200})

    log = tc.format_trace_log()
    # 不再是 "Tokens: 0"
    assert "Tokens: 700" in log
