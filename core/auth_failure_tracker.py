"""Auth failure tracker — 自動 anomaly detection(GAP-1 根治)。

職責
====
原本 SecurityMonitor 的 12 種事件 type(含 BRUTE_FORCE_ATTEMPT)全是 dead code —
沒有任何地方呼叫 log_event,也沒有自動偵測規則。本模組提供「auth 失敗計數 →
自動偵測異常」機制,在 auth 失敗點呼叫即可繼承。

可配置(env-driven,預設安全 — 只偵測記錄,不鎖定):
- AUTH_FAILURE_THRESHOLD:窗口內失敗次數閾值(預設 10)
- AUTH_FAILURE_WINDOW_SECONDS:滑動窗口秒數(預設 600 = 10 分鐘)
- AUTH_LOCKOUT_ENABLED:超閾值是否鎖定(預設 false — 只記錄,不擋登入)
- AUTH_LOCKOUT_THRESHOLD:觸發**阻擋**的次數(預設 30,與告警的 10 分開)

為什麼預設不鎖定:這個站登入是 TON wallet / Telegram proof(非密碼),「失敗」
可能是使用者換錢包、proof 過期等正常情況,鎖定會誤傷。預設只偵測 + 發
BRUTE_FORCE_ATTEMPT event(SecurityMonitor 記錄 + HIGH severity telegram alert),
DANNY 確認門檻後設 AUTH_LOCKOUT_ENABLED=true 才實際鎖定。

設計:per-IP in-memory sliding window。要跨 worker 可之後接 Redis(同 GAP-2 模式),
但偵測層不是硬安全邊界(鎖定才是),in-memory 各 worker 獨立計數可接受。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 可配置參數(env-driven,預設安全)
# ──────────────────────────────────────────────────────────────────────────────


def _threshold() -> int:
    """窗口內失敗次數閾值。"""
    try:
        return int(os.getenv("AUTH_FAILURE_THRESHOLD", "10"))
    except ValueError:
        return 10


def _window_seconds() -> int:
    """滑動窗口秒數。"""
    try:
        return int(os.getenv("AUTH_FAILURE_WINDOW_SECONDS", "600"))
    except ValueError:
        return 600


def _lockout_threshold() -> int:
    """觸發**阻擋**的失敗次數。刻意與 _threshold()(觸發告警)分開。

    共用一個閾值會逼你二選一:調高才不會誤鎖,但調高就同時讓告警變遲鈍,
    等於為了少誤鎖而放棄偵測。分開之後告警維持 10 次(敏感,你才看得到
    真實分佈),阻擋預設 30 次(保守,錢包簽章的失敗率天生比密碼高)。
    """
    try:
        return int(os.getenv("AUTH_LOCKOUT_THRESHOLD", "30"))
    except ValueError:
        return 30


def _lockout_enabled() -> bool:
    """超閾值是否鎖定。預設 false(只偵測記錄)。"""
    from core.feature_flags import env_flag

    return env_flag("AUTH_LOCKOUT_ENABLED", "false")


# ──────────────────────────────────────────────────────────────────────────────
# Per-IP sliding window 計數器
# ──────────────────────────────────────────────────────────────────────────────

_failures: Dict[str, Deque[float]] = defaultdict(deque)
_lock: threading.Lock = threading.Lock()


def _prune_window(ip: str, now: float, window: int) -> None:
    """移除窗口外的過期記錄(必須在 lock 內呼叫)。"""
    cutoff = now - window
    dq = _failures.get(ip)
    if not dq:
        return
    while dq and dq[0] < cutoff:
        dq.popleft()
    if not dq:
        del _failures[ip]


def record_auth_failure(ip: str, detail: str = "") -> bool:
    """記錄一次 auth 失敗,回傳是否觸發異常(達閾值)。

    達閾值時自動發 BRUTE_FORCE_ATTEMPT event 給 SecurityMonitor(記錄 + alert)。
    若 AUTH_LOCKOUT_ENABLED=true,後續 is_locked_out(ip) 會回 True。

    Args:
        ip: 客戶端 IP
        detail: 失敗原因描述(進 event metadata,不含敏感資料)

    Returns:
        True = 本次失敗觸發異常偵測(達閾值)
    """
    if not ip:
        return False

    now = time.time()
    window = _window_seconds()
    threshold = _threshold()

    with _lock:
        _prune_window(ip, now, window)
        _failures[ip].append(now)
        count = len(_failures[ip])

    # 恰好達閾值時觸發(避免每次失敗都重複發 event)
    triggered = count >= threshold and count % threshold == 0
    if triggered:
        _emit_brute_force_event(ip, count, window, detail)

    return triggered


def is_locked_out(ip: str) -> bool:
    """該 IP 是否被鎖定(只在 AUTH_LOCKOUT_ENABLED=true 時有意義)。

    鎖定條件:窗口內失敗數 ≥ AUTH_LOCKOUT_THRESHOLD。鎖定持續到窗口滑過
    (失敗記錄過期),沒有另外的解鎖流程——所以閾值寧可保守。

    ⚠️ 這一層是偵測級不是硬安全邊界:per-IP、in-memory、每個 worker 各自
    計數(N 個 worker 實際閾值就是 N 倍),重啟清空。擋得住笨的暴力嘗試,
    擋不住分散式來源。要當真正的邊界得接 Redis。
    """
    if not _lockout_enabled() or not ip:
        return False

    now = time.time()
    window = _window_seconds()
    threshold = _lockout_threshold()

    with _lock:
        _prune_window(ip, now, window)
        count = len(_failures.get(ip, []))

    return count >= threshold


def get_failure_count(ip: str) -> int:
    """該 IP 目前窗口內的失敗次數(偵錯/監控用)。"""
    now = time.time()
    window = _window_seconds()
    with _lock:
        _prune_window(ip, now, window)
        return len(_failures.get(ip, []))


def reset_failures(ip: str) -> None:
    """清除該 IP 的失敗記錄(登入成功後可呼叫,給使用者乾淨起點)。"""
    with _lock:
        _failures.pop(ip, None)


def _emit_brute_force_event(ip: str, count: int, window: int, detail: str) -> None:
    """發 BRUTE_FORCE_ATTEMPT event 給 SecurityMonitor。"""
    try:
        from core.security_monitor import (
            SecurityEventType,
            SeverityLevel,
            log_security_event,
        )

        log_security_event(
            event_type=SecurityEventType.BRUTE_FORCE_ATTEMPT,
            severity=SeverityLevel.HIGH,
            title=f"Multiple auth failures from {ip}",
            description=(
                f"{count} auth failures from IP {ip} within {window}s window"
                + (f" ({detail})" if detail else "")
            ),
            ip_address=ip,
            metadata={"failure_count": count, "window_seconds": window, "detail": detail},
        )
        logger.warning(
            "[AuthFailureTracker] BRUTE_FORCE detected: %d failures from %s in %ds",
            count,
            ip,
            window,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        # SecurityMonitor 故障不該擋 auth 流程
        logger.error("[AuthFailureTracker] failed to emit event: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# 重置(測試用)
# ──────────────────────────────────────────────────────────────────────────────


def _reset_all_for_test() -> None:
    """測試用:清空所有失敗記錄。"""
    with _lock:
        _failures.clear()
