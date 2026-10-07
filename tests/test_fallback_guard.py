"""Fallback guard 測試 — turn-scoped + cascade 防護（#③）。

學 Hermes fallback-providers doc + issue #24996。fallback 保持停用
（is_fallback_enabled 恆 False，因為沒設 env），但 guard 邏輯本身可測。
"""

from __future__ import annotations

import time

import core.agents.fallback as fb


def test_turn_guard_resets_between_turns():
    """每個 turn reset 後，計數歸零。"""
    fb._turn_fallback_count = 2  # 模擬上一輪用了
    fb.reset_turn_fallback_guard()
    assert fb._turn_fallback_count == 0


def test_cascade_breaker_trips_after_max(monkeypatch):
    """窗口內累計超 MAX_CASCADE_FALLBACKS → 熔斷。"""
    # 模擬窗口內已有 MAX_CASCADE_FALLBACKS 次時間戳
    now = time.monotonic()
    fb._cascade_timestamps = [now, now, now]  # 3 次 = MAX
    assert fb._cascade_tripped() is True


def test_cascade_breaker_expires_after_window(monkeypatch):
    """過期的時間戳被清掉，不再熔斷。"""
    now = time.monotonic()
    # 超過窗口的舊時間戳
    fb._cascade_timestamps = [now - fb.CASCADE_WINDOW_SECONDS - 10] * 3
    assert fb._cascade_tripped() is False


def test_should_fallback_disabled_by_default():
    """沒設 env → fallback 恆停用。"""
    assert fb.is_fallback_enabled() is False
    assert (
        fb.should_fallback(final_response="", used_tools=[], retry_exhausted=True)
        is False
    )


def test_should_fallback_respects_turn_limit(monkeypatch):
    """turn-scoped：本輪已 fallback 過 → 不再 fallback。"""
    # 模擬啟用（繞過 env 檢查）
    monkeypatch.setattr(fb, "is_fallback_enabled", lambda: True)
    fb._turn_fallback_count = fb.MAX_FALLBACKS_PER_TURN
    fb._cascade_timestamps = []  # 未熔斷
    assert (
        fb.should_fallback(final_response="", used_tools=[], retry_exhausted=True)
        is False
    )


def test_should_fallback_capacity_error_triggers(monkeypatch):
    """capacity error（402/quota）直接觸發 fallback，不需 retry_exhausted。"""
    monkeypatch.setattr(fb, "is_fallback_enabled", lambda: True)
    fb._turn_fallback_count = 0
    fb._cascade_timestamps = []
    err = Exception("402 insufficient credits, daily quota exceeded")
    assert (
        fb.should_fallback(
            final_response="", used_tools=[], retry_exhausted=False, error=err
        )
        is True
    )


def test_mark_fallback_used_increments(monkeypatch):
    """mark_fallback_used 增計數（turn + cascade）。"""
    fb._turn_fallback_count = 0
    fb._cascade_timestamps = []
    fb.mark_fallback_used()
    assert fb._turn_fallback_count == 1
    assert len(fb._cascade_timestamps) == 1
