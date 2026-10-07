"""確認卡 resume 的結果快取（claw_loop，2026-10-06）。

LangGraph node-level interrupt 在使用者答完後從 node 開頭重跑：agent loop 整個再跑一輪，
只為了走回 interrupt() 取答案。本機實測按「確認記入」會多出 3 次 LLM 呼叫（全工具時約 30 秒），
回覆還把提案文字重貼一次。修法：彈卡前存 agent 結果，resume 重跑時直接取用。

守的是：快取的 key／TTL／容量／清除；接線（彈卡前存、重跑時取、處理完清、回覆只留處理結果）；
以及一個真實踩到的洞——resume 那一輪 _tool_steps.total 是 0，不能被當成「零工具回覆」塞進 response_cache
（否則同一題再問會拿到沒有確認卡的提案文字）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agents.manager import claw_loop as cl

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean():
    cl._resume_results.clear()
    yield
    cl._resume_results.clear()


def test_stash_peek_discard_roundtrip():
    key = cl._resume_result_key("u1", "s1", "幫我記一筆午餐 120")
    assert cl._peek_resume_result(key) is None
    sentinel = object()
    cl._stash_resume_result(key, sentinel)
    # peek 不消耗：resume 可能連續兩次重跑（多張卡）
    assert cl._peek_resume_result(key) is sentinel
    assert cl._peek_resume_result(key) is sentinel
    cl._discard_resume_result(key)
    assert cl._peek_resume_result(key) is None
    cl._discard_resume_result(key)  # 重複清除不報錯


def test_key_is_per_user_session_and_normalized_query():
    a = cl._resume_result_key("u1", "s1", "幫我記一筆午餐 120！")
    assert a == cl._resume_result_key("u1", "s1", "  幫我記一筆午餐 120 ")
    assert a != cl._resume_result_key("u2", "s1", "幫我記一筆午餐 120")
    assert a != cl._resume_result_key("u1", "s2", "幫我記一筆午餐 120")
    assert a != cl._resume_result_key("u1", "s1", "幫我記一筆晚餐 120")
    # 沒有 session 也不炸
    assert cl._resume_result_key(None, None, "x") == ("", "", "x")


def test_expired_entries_are_not_served(monkeypatch):
    key = cl._resume_result_key("u1", "s1", "q")
    now = [1000.0]
    monkeypatch.setattr(cl.time, "monotonic", lambda: now[0])
    cl._stash_resume_result(key, "result")
    assert cl._peek_resume_result(key) == "result"
    now[0] += cl._RESUME_RESULT_TTL_S + 1
    assert cl._peek_resume_result(key) is None
    assert key not in cl._resume_results


def test_capacity_is_bounded_and_oldest_goes_first():
    for i in range(cl._RESUME_RESULT_MAX + 20):
        cl._stash_resume_result(cl._resume_result_key("u", "s", f"q{i}"), i)
    assert len(cl._resume_results) <= cl._RESUME_RESULT_MAX
    assert cl._peek_resume_result(cl._resume_result_key("u", "s", "q0")) is None
    last = cl._RESUME_RESULT_MAX + 19
    assert cl._peek_resume_result(cl._resume_result_key("u", "s", f"q{last}")) == last


def test_wiring_in_the_node():
    src = (REPO / "core/agents/manager/claw_loop.py").read_text(encoding="utf-8")
    # 彈卡前存
    stash = src.index("_stash_resume_result(_resume_key, result)")
    interrupt = src.index(
        "answer = interrupt(payload)  # pauses graph; resume lands here"
    )
    assert stash < interrupt
    # 重跑時取：在 _run_agent 之前
    peek = src.index("_cached_result = _peek_resume_result(_resume_key)")
    run = src.index("result = await _run_agent(task)", peek)
    assert peek < run
    # 確認卡處理完（沒再 interrupt）就清掉
    assert "_discard_resume_result(_resume_key)" in src
    # 重用結果時回覆只留處理結果，且不能寫進 response_cache
    assert "if _resumed_from_cache and _outcome_text" in src
    guard = src.index("and not _resumed_from_cache")
    put = src.index("response_cache.put(", guard)
    assert guard < put
