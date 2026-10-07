"""
BYOK Fallback Provider Chain 單元測試。

覆蓋 ``core/agents/fallback.py`` 的三個函式。
所有 case 都在 fallback **未啟用**狀態下測試（因為啟用需要真實 env keys）。
"""
from __future__ import annotations

import importlib

import pytest

from core.agents import fallback


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """每個 test 前：清掉所有 BYOK_FALLBACK_* env、reload module。"""
    for k in list(__import__("os").environ.keys()):
        if k.startswith("BYOK_FALLBACK_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TEST_MODE", "true")  # 預設測試模式
    importlib.reload(fallback)
    yield
    importlib.reload(fallback)


def test_fallback_disabled_by_default():
    """預設情況下 fallback 不啟用。"""
    assert fallback.is_fallback_enabled() is False


def test_fallback_disabled_in_test_mode(monkeypatch):
    """即使設了所有 env，TEST_MODE=true 也不啟用。"""
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "sk-test")
    importlib.reload(fallback)
    assert fallback.is_fallback_enabled() is False


def test_fallback_disabled_when_flag_off(monkeypatch):
    """非 TEST_MODE 但 ENABLED 未設。"""
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "sk-test")
    importlib.reload(fallback)
    assert fallback.is_fallback_enabled() is False


def test_fallback_disabled_when_provider_missing(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    # BYOK_FALLBACK_PROVIDER 沒設
    monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "sk-test")
    importlib.reload(fallback)
    assert fallback.is_fallback_enabled() is False


def test_fallback_disabled_when_api_key_missing(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "openai")
    # BYOK_FALLBACK_API_KEY 沒設
    importlib.reload(fallback)
    assert fallback.is_fallback_enabled() is False


def test_fallback_enabled_when_all_env_set(monkeypatch):
    """非 TEST_MODE + 全部 env 設好 → 啟用。"""
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "sk-test")
    monkeypatch.setenv("BYOK_FALLBACK_MODEL", "gpt-4o-mini")
    importlib.reload(fallback)
    assert fallback.is_fallback_enabled() is True


# ============================================================================
# should_fallback
# ============================================================================


def test_should_fallback_returns_false_when_disabled():
    """未啟用 → 永遠 False。"""
    assert fallback.should_fallback("", [], retry_exhausted=True) is False


def test_should_fallback_returns_false_when_retry_not_exhausted(monkeypatch):
    """recovery 還沒用完 → 不 fallback。"""
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "sk-test")
    importlib.reload(fallback)
    assert fallback.should_fallback("", [], retry_exhausted=False) is False


def test_should_fallback_returns_false_when_response_has_content(monkeypatch):
    """有實質內容（>50 字）→ 不 fallback（避免浪費 server key）。"""
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "sk-test")
    importlib.reload(fallback)
    long_response = "BTC 現價 $64,000，建議分批佈局。" + "x" * 100
    assert fallback.should_fallback(long_response, [], retry_exhausted=True) is False


def test_should_fallback_returns_true_when_all_conditions_met(monkeypatch):
    """啟用 + retry 用完 + 回應空 → 該 fallback。"""
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "sk-test")
    importlib.reload(fallback)
    assert fallback.should_fallback("", [], retry_exhausted=True) is True
    # 短回應（fallback message）也該觸發
    assert fallback.should_fallback("模型沒有產生回應", [], retry_exhausted=True) is True


# ============================================================================
# get_fallback_client
# ============================================================================


def test_get_fallback_client_returns_none_when_disabled():
    """未啟用 → None。"""
    assert fallback.get_fallback_client() is None


def test_get_fallback_client_returns_client_when_enabled(monkeypatch):
    """啟用且 env 完整 → 回傳 client（用 mock provider 避免真實連線）。"""
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("BYOK_FALLBACK_API_KEY", "sk-test-placeholder")
    monkeypatch.setenv("BYOK_FALLBACK_MODEL", "gpt-4o-mini")
    importlib.reload(fallback)
    client = fallback.get_fallback_client()
    assert client is not None
    assert client.model_name == "gpt-4o-mini"
