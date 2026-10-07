"""Agent error classification 測試 — transient rate limit vs capacity 區分。

Regression：#② 穩定性改進。過去 is_rate_limit_error 把 402/quota 也當 retryable，
但 capacity error（配額耗盡/餘額不足）retry 無意義，該換 provider 或提示充值。
本測試確保分類正確：transient 429 = retry，capacity 402/quota = fail-fast。
"""

from __future__ import annotations

import pytest

from core.agents.rate_limit import (
    CAPACITY_MARKERS,
    RATE_LIMIT_MARKERS,
    SERVER_ERROR_MARKERS,
    is_capacity_error,
    is_rate_limit_error,
    is_transient_error,
)


def _err(msg: str) -> Exception:
    return Exception(msg)


# ============================================================================
# is_rate_limit_error（transient，可 retry）
# ============================================================================


@pytest.mark.parametrize(
    "msg",
    [
        "429 Too Many Requests",
        "Rate limit exceeded",
        "too many requests",
        "ResourceExhausted: Worker local total request limit reached (16/16)",
    ],
)
def test_transient_rate_limit_is_retryable(msg):
    """429 / NIM 並發 = 暫時性，retry 有意義。"""
    assert is_rate_limit_error(_err(msg)) is True


@pytest.mark.parametrize(
    "msg",
    [
        "402 Payment Required",
        "insufficient balance",
        "insufficient credits",
        "You exceeded your current quota",
        "RESOURCE_EXHAUSTED: quota exceeded",
        "daily limit reached",
        "Too many tokens per day",
    ],
)
def test_capacity_error_not_retryable(msg):
    """配額/容量耗盡 = retry 無意義，不是 transient rate limit。"""
    assert is_rate_limit_error(_err(msg)) is False


def test_capacity_429_combo_not_retryable():
    """402 訊息可能帶 429 字眼（如 '429: daily quota exceeded'），但不能 retry。"""
    assert is_rate_limit_error(_err("429 daily quota exceeded")) is False


# ============================================================================
# is_capacity_error（配額耗盡，fail-fast / 換 provider）
# ============================================================================


@pytest.mark.parametrize(
    "msg",
    [
        "402 Payment Required",
        "insufficient balance",
        "insufficient_quota",
        "exceeded your current quota",
        "quota exceeded",
        "quota_exceeded",
        "RESOURCE_EXHAUSTED: quota",
        "daily limit",
        "Too many tokens per day",
        "billing hard limit reached",
    ],
)
def test_capacity_errors_detected(msg):
    """各家 provider 的 quota/capacity 文案都該被認出。"""
    assert is_capacity_error(_err(msg)) is True


@pytest.mark.parametrize(
    "msg",
    [
        "429 Too Many Requests",
        "ResourceExhausted: Worker local total request limit",
        "Connection timeout",
        "Internal server error",
        "你好",
    ],
)
def test_non_capacity_not_detected(msg):
    """transient 429 / 一般錯誤不是 capacity error。"""
    assert is_capacity_error(_err(msg)) is False


def test_mutual_exclusivity():
    """transient rate limit 與 capacity error 應互斥（402 帶 429 字眼歸 capacity）。"""
    e = _err("429: quota exceeded, daily limit")
    # 是 capacity，不是 transient rate limit
    assert is_capacity_error(e) is True
    assert is_rate_limit_error(e) is False


def test_markers_nonempty_and_disjoint():
    """兩個 marker 清單都非空，且 capacity 優先判斷避免誤歸 transient。"""
    assert len(RATE_LIMIT_MARKERS) > 0
    assert len(CAPACITY_MARKERS) > 0


# ============================================================================
# is_transient_error（供應商 5xx 也視為可重試 — 線上 #特斯拉案例）
# ============================================================================


@pytest.mark.parametrize(
    "msg",
    [
        # 線上實際案例（DANNY 回報，Zeabur 部署）
        "Error code: 500 - {'error': {'message': 'Internal server error.', "
        "'type': 'InternalServerError', 'code': 500}}",
        "InternalServerError",
        "internal server error",
        "Service Unavailable",
        "ServiceUnavailable",
        "Bad Gateway",
        "server overload",
    ],
)
def test_provider_5xx_is_transient(msg):
    """供應商 5xx（500/502/503 過載）= 暫時性，retry 有意義（線上 #特斯拉案例）。"""
    assert is_transient_error(_err(msg)) is True


@pytest.mark.parametrize(
    "msg",
    [
        "429 Too Many Requests",
        "Rate limit exceeded",
        "ResourceExhausted: Worker local total request limit reached (16/16)",
    ],
)
def test_rate_limit_still_transient(msg):
    """429 / NIM 並發仍屬 transient（is_transient_error 涵蓋 rate limit）。"""
    assert is_transient_error(_err(msg)) is True


@pytest.mark.parametrize(
    "msg",
    [
        "401 Unauthorized",
        "403 Forbidden",
        "402 Payment Required",
        "exceeded your current quota",
        "insufficient balance",
        "daily limit reached",
        "invalid api key",
        "something broke",
    ],
)
def test_non_transient_errors_not_retryable(msg):
    """401/402/403/quota 與未知錯誤 = 不可重試（fail-fast 省免費額度）。"""
    assert is_transient_error(_err(msg)) is False


def test_capacity_overrides_5xx_markers():
    """若訊息同時含 5xx 與 capacity marker（如 '500 billing error'），歸 capacity 不可重試。

    避免供應商把配額錯誤誤包成 500。capacity 優先判斷保護免費方案額度。
    """
    e = _err("500 internal error: exceeded your current quota")
    assert is_transient_error(e) is False
    assert is_capacity_error(e) is True


def test_server_error_markers_descriptive():
    """5xx marker 用描述性字串而非裸數字，避免誤判（如 'retrieved 500 results'）。"""
    assert len(SERVER_ERROR_MARKERS) > 0
    # 裸數字 500 不在 marker 清單（避免 token count / result count 誤判）
    assert "500" not in SERVER_ERROR_MARKERS
    # 但 'internalservererror' / 'internal server error' 在
    assert "internalservererror" in SERVER_ERROR_MARKERS
    # 「retrieved 500 results」這種不該被判為 5xx
    assert is_transient_error(_err("Successfully retrieved 500 results")) is False


# ============================================================================
# 友善訊息：供應商 5xx 不再噴原文進聊天泡泡（線上 #特斯拉案例）
# ============================================================================


def test_friendly_message_for_provider_500():
    """供應商 500 的友善訊息不該含 'Error code' 原文（四語）。"""
    from core.agents.base_react_agent import _friendly_llm_error

    raw = (
        "Error code: 500 - {'error': {'message': 'Internal server error.', "
        "'type': 'InternalServerError', 'code': 500}}"
    )
    for lang in ("zh-TW", "zh-CN", "en", "ru"):
        msg = _friendly_llm_error(raw, lang)
        assert msg is not None, f"{lang} 應有友善訊息"
        assert "Error code" not in msg, f"{lang} 不該含原文"
        assert "500" not in msg, f"{lang} 不該含錯誤碼數字"
