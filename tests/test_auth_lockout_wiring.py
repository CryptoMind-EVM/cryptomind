"""AUTH_LOCKOUT_ENABLED 真的有接上的守衛。

2026-09-06 發現它是 **phantom flag**：`is_locked_out()` 寫好了、單元測試也
綠，但 production code 一處都沒有呼叫。把 `AUTH_LOCKOUT_ENABLED=true` 設下去，
唯一的效果是讓一個沒人問的函式改變回傳值——而你會以為自己開了防護。

這一組測試盯的就是「線有沒有斷掉」，不是「函式對不對」（那在
test_auth_failure_tracker.py）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
USER_ROUTER = ROOT / "api/routers/user.py"


@pytest.fixture(scope="module")
def src() -> str:
    return USER_ROUTER.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """剝掉 # 註解與 docstring——不剝的話註解提到函式名就能滿足守衛。"""
    text = re.sub(r'"""(?:.|\n)*?"""', "", text)
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


# ── 線有沒有接上 ────────────────────────────────────────────────────────


def test_lockout_is_actually_enforced_somewhere(src: str):
    """旗標開了要真的擋得住人，不是只讓一個函式回 True。

    只檢查「檔案裡有沒有 _enforce_auth_lockout 這個字」不夠——helper 的
    **定義**本身就含有它，把所有呼叫點刪光這個守衛照樣綠（實測過）。
    要數的是**呼叫點**，不是出現次數。
    """
    code = _strip_comments(src)
    calls = re.findall(r"^\s+_enforce_auth_lockout\(", code, re.M)
    assert calls, "沒有任何地方執行鎖定 = phantom flag，旗標開了也不會擋人"


def test_every_endpoint_that_records_failures_also_enforces(src: str):
    """會記錄失敗的端點就該受鎖定保護，否則攻擊者挑沒接的那個打就好。

    比對方式是「取得 client_ip 的函式」——記錄與阻擋都以 client_ip 為單位。
    """
    code = _strip_comments(src)
    ip_lines = len(re.findall(r"^\s+client_ip = get_remote_address\(request\)", code, re.M))
    enforced = len(re.findall(r"^\s+_enforce_auth_lockout\(client_ip\)", code, re.M))
    assert ip_lines > 0
    assert enforced == ip_lines, (
        f"{ip_lines} 個端點取了 client_ip，只有 {enforced} 個有擋。"
        f"沒擋的那個就是攻擊者會挑的那個。"
    )


def test_enforcement_comes_before_the_failure_is_recorded(src: str):
    """先擋再驗證，否則被鎖的 IP 每次請求還是會讓計數繼續長，永遠解不開。"""
    code = _strip_comments(src)
    for m in re.finditer(r"^\s+client_ip = get_remote_address\(request\)", code, re.M):
        after = code[m.end() : m.end() + 400]
        enforce_at = after.find("_enforce_auth_lockout")
        record_at = after.find("record_auth_failure(")
        assert enforce_at != -1, "取了 client_ip 卻沒擋"
        if record_at != -1:
            assert enforce_at < record_at, "阻擋要排在記錄失敗之前"


def test_successful_login_clears_the_counter(src: str):
    """不清的話，失敗 9 次再成功的人仍背著 9 次，下次很容易被鎖。"""
    code = _strip_comments(src)
    assert "_clear_auth_failures(client_ip)" in code
    cleared = len(re.findall(r"_clear_auth_failures\(client_ip\)", code))
    assert cleared >= 5, f"只有 {cleared} 條成功路徑清了計數"


def test_lockout_returns_429_not_401(src: str):
    """回 401 會讓錢包 App 引導使用者去重新綁定——那不是憑證問題是頻率問題。"""
    start = src.index("def _enforce_auth_lockout")
    block = src[start : start + 1200]
    assert "status_code=429" in block
    assert "Retry-After" in block, "沒有 Retry-After，客戶端不知道要等多久"


# ── 閾值設計 ────────────────────────────────────────────────────────────


def test_block_threshold_is_separate_from_alert_threshold():
    """共用一個閾值就得二選一：調高才不會誤鎖，調高卻讓告警變遲鈍。"""
    from core.auth_failure_tracker import _lockout_threshold, _threshold

    assert _threshold() != _lockout_threshold()
    assert _lockout_threshold() > _threshold(), "阻擋應該比告警保守"


def test_lockout_defaults_to_off(monkeypatch):
    """錢包簽章的失敗有大量良性情況（換錢包、proof 過期），預設不能擋。"""
    from core.auth_failure_tracker import _lockout_enabled

    monkeypatch.delenv("AUTH_LOCKOUT_ENABLED", raising=False)
    assert _lockout_enabled() is False


def test_flag_off_means_never_locked(monkeypatch):
    import core.auth_failure_tracker as t

    monkeypatch.delenv("AUTH_LOCKOUT_ENABLED", raising=False)
    t.reset_failures("8.8.8.8")
    for _ in range(100):
        t.record_auth_failure("8.8.8.8", "test")
    assert t.is_locked_out("8.8.8.8") is False


def test_flag_on_locks_only_past_the_block_threshold(monkeypatch):
    import core.auth_failure_tracker as t

    monkeypatch.setenv("AUTH_LOCKOUT_ENABLED", "true")
    monkeypatch.setenv("AUTH_FAILURE_THRESHOLD", "10")
    monkeypatch.setenv("AUTH_LOCKOUT_THRESHOLD", "30")
    t.reset_failures("7.7.7.7")
    for _ in range(15):
        t.record_auth_failure("7.7.7.7", "test")
    assert t.is_locked_out("7.7.7.7") is False, "告警線不該等於阻擋線"
    for _ in range(20):
        t.record_auth_failure("7.7.7.7", "test")
    assert t.is_locked_out("7.7.7.7") is True


def test_flag_is_registered_for_audit():
    from core.feature_flags import FLAG_REGISTRY

    assert "AUTH_LOCKOUT_ENABLED" in FLAG_REGISTRY
