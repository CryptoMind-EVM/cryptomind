"""Auth failure tracker 測試 — core/auth_failure_tracker.py(GAP-1)。

依 AGENTS.md「success / reject / edge」:
- success:失敗計數正確、達閾值觸發 BRUTE_FORCE_ATTEMPT event
- reject(防誤判):單次失敗不觸發、成功重置、不同 IP 獨立
- edge:空 IP、窗口過期、可配置參數、鎖定開關

背景:SecurityMonitor 的 12 種事件原本全是 dead code,無自動偵測。
tracker 在 auth 失敗點計數,達閾值自動發 event。預設只記錄不鎖定(不破壞 UX)。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core import auth_failure_tracker


@pytest.fixture(autouse=True)
def _reset_state():
    """每個測試前清空失敗記錄,避免污染。"""
    auth_failure_tracker._reset_all_for_test()
    yield
    auth_failure_tracker._reset_all_for_test()


# ──────────────────────────────────────────────────────────────────────────────
# Success — 失敗計數 + 達閾值觸發
# ──────────────────────────────────────────────────────────────────────────────


def test_single_failure_does_not_trigger(monkeypatch):
    """單次失敗不該觸發(未達閾值)。"""
    monkeypatch.setenv("AUTH_FAILURE_THRESHOLD", "5")
    assert auth_failure_tracker.record_auth_failure("1.2.3.4", "test") is False
    assert auth_failure_tracker.get_failure_count("1.2.3.4") == 1


def test_threshold_triggers_event(monkeypatch):
    """達閾值時觸發,發 BRUTE_FORCE_ATTEMPT event。"""
    monkeypatch.setenv("AUTH_FAILURE_THRESHOLD", "3")
    with patch("core.auth_failure_tracker._emit_brute_force_event") as mock_emit:
        auth_failure_tracker.record_auth_failure("1.2.3.4")  # 1
        assert mock_emit.call_count == 0
        auth_failure_tracker.record_auth_failure("1.2.3.4")  # 2
        assert mock_emit.call_count == 0
        triggered = auth_failure_tracker.record_auth_failure("1.2.3.4")  # 3 = threshold
        assert triggered is True
        assert mock_emit.call_count == 1


def test_repeated_threshold_re_triggers(monkeypatch):
    """每達 threshold 倍數重新觸發(6 = 3*2),避免每次失敗都重複發。"""
    monkeypatch.setenv("AUTH_FAILURE_THRESHOLD", "3")
    with patch("core.auth_failure_tracker._emit_brute_force_event") as mock_emit:
        for _ in range(3):
            auth_failure_tracker.record_auth_failure("1.2.3.4")
        assert mock_emit.call_count == 1  # 第 3 次觸發
        auth_failure_tracker.record_auth_failure("1.2.3.4")  # 4
        assert mock_emit.call_count == 1  # 未達 6,不重觸發
        auth_failure_tracker.record_auth_failure("1.2.3.4")  # 5
        assert mock_emit.call_count == 1
        auth_failure_tracker.record_auth_failure("1.2.3.4")  # 6 = 3*2
        assert mock_emit.call_count == 2  # 第 6 次重觸發


def test_emit_event_calls_security_monitor():
    """_emit_brute_force_event 真的呼叫 log_security_event。"""
    with patch("core.security_monitor.log_security_event") as mock_log:
        auth_failure_tracker._emit_brute_force_event("1.2.3.4", 5, 600, "test")
    mock_log.assert_called_once()
    call = mock_log.call_args
    assert "BRUTE_FORCE" in str(call.kwargs.get("event_type")) or "brute" in str(
        call.kwargs.get("event_type")
    )


# ──────────────────────────────────────────────────────────────────────────────
# Reject — 防「誤判」正常行為
# ──────────────────────────────────────────────────────────────────────────────


def test_different_ips_independent(monkeypatch):
    """不同 IP 的失敗計數獨立。"""
    monkeypatch.setenv("AUTH_FAILURE_THRESHOLD", "3")
    auth_failure_tracker.record_auth_failure("1.1.1.1")
    auth_failure_tracker.record_auth_failure("1.1.1.1")
    auth_failure_tracker.record_auth_failure("2.2.2.2")
    assert auth_failure_tracker.get_failure_count("1.1.1.1") == 2
    assert auth_failure_tracker.get_failure_count("2.2.2.2") == 1


def test_reset_after_success():
    """reset_failures 清空該 IP 記錄(登入成功後給乾淨起點)。"""
    auth_failure_tracker.record_auth_failure("1.2.3.4")
    auth_failure_tracker.record_auth_failure("1.2.3.4")
    assert auth_failure_tracker.get_failure_count("1.2.3.4") == 2
    auth_failure_tracker.reset_failures("1.2.3.4")
    assert auth_failure_tracker.get_failure_count("1.2.3.4") == 0


def test_window_expiry(monkeypatch):
    """窗口外的失敗記錄過期,不再計入。"""
    monkeypatch.setenv("AUTH_FAILURE_WINDOW_SECONDS", "1")
    monkeypatch.setenv("AUTH_FAILURE_THRESHOLD", "5")
    auth_failure_tracker.record_auth_failure("1.2.3.4")
    auth_failure_tracker.record_auth_failure("1.2.3.4")
    # 模擬時間經過窗口外
    import time

    with patch("core.auth_failure_tracker.time.time", return_value=time.time() + 2):
        assert auth_failure_tracker.get_failure_count("1.2.3.4") == 0  # 已過期


# ──────────────────────────────────────────────────────────────────────────────
# Edge — 空值 / 鎖定開關 / 可配置
# ──────────────────────────────────────────────────────────────────────────────


def test_empty_ip_ignored():
    """空 IP 不記錄(避免 None/空字串汙染計數器)。"""
    assert auth_failure_tracker.record_auth_failure("") is False
    assert auth_failure_tracker.record_auth_failure(None) is False
    assert auth_failure_tracker.get_failure_count("") == 0


def test_lockout_disabled_by_default(monkeypatch):
    """預設 AUTH_LOCKOUT_ENABLED=false → is_locked_out 永遠 False。"""
    monkeypatch.setenv("AUTH_LOCKOUT_ENABLED", "false")
    monkeypatch.setenv("AUTH_FAILURE_THRESHOLD", "2")
    auth_failure_tracker.record_auth_failure("1.2.3.4")
    auth_failure_tracker.record_auth_failure("1.2.3.4")
    assert auth_failure_tracker.is_locked_out("1.2.3.4") is False


def test_lockout_enabled_when_set(monkeypatch):
    """設 AUTH_LOCKOUT_ENABLED=true 且達**阻擋**閾值 → is_locked_out True。

    2026-09-06 改：阻擋改看 AUTH_LOCKOUT_THRESHOLD，不再與告警的
    AUTH_FAILURE_THRESHOLD 共用。共用會逼你二選一——調高才不會誤鎖，
    但調高就同時讓告警變遲鈍。這個測試原本設 AUTH_FAILURE_THRESHOLD=2
    就期待鎖定，那是舊設計。
    """
    monkeypatch.setenv("AUTH_LOCKOUT_ENABLED", "true")
    monkeypatch.setenv("AUTH_LOCKOUT_THRESHOLD", "2")
    auth_failure_tracker.record_auth_failure("1.2.3.4")
    assert auth_failure_tracker.is_locked_out("1.2.3.4") is False  # 1 < 2
    auth_failure_tracker.record_auth_failure("1.2.3.4")
    assert auth_failure_tracker.is_locked_out("1.2.3.4") is True  # 2 >= 2


def test_alert_threshold_alone_does_not_lock(monkeypatch):
    """達到告警線不等於達到阻擋線——這正是拆開兩個閾值的目的。"""
    monkeypatch.setenv("AUTH_LOCKOUT_ENABLED", "true")
    monkeypatch.setenv("AUTH_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("AUTH_LOCKOUT_THRESHOLD", "10")
    auth_failure_tracker.reset_failures("5.5.5.5")
    for _ in range(5):
        auth_failure_tracker.record_auth_failure("5.5.5.5")
    assert auth_failure_tracker.is_locked_out("5.5.5.5") is False


def test_configurable_threshold(monkeypatch):
    """閾值可由 env 調整。"""
    monkeypatch.setenv("AUTH_FAILURE_THRESHOLD", "100")
    for _ in range(50):
        auth_failure_tracker.record_auth_failure("1.2.3.4")
    assert auth_failure_tracker.get_failure_count("1.2.3.4") == 50  # 沒觸發


def test_invalid_env_falls_back_to_default(monkeypatch):
    """無效的 env 值 → fallback 預設。"""
    monkeypatch.setenv("AUTH_FAILURE_THRESHOLD", "not-a-number")
    assert auth_failure_tracker._threshold() == 10  # 預設
