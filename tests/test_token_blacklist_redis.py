"""Token blacklist Redis 支援測試 — api/deps.py(GAP-2)。

degrade-safe 設計:Redis 可用 → 跨 worker 共享;無 Redis → 退回 file-backed。
本測試驗證兩條 path 都正確,且切換不破壞既有行為。

用 fakeredis 模擬 Redis(若裝了);否則 mock client 測 degrade 路徑。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from api import deps


@pytest.fixture(autouse=True)
def _reset_redis_cache():
    """每個測試前重置 Redis client cache,確保 _get_revoked_redis_client 重評估。"""
    deps._revoked_redis = None
    deps._revoked_redis_checked = False
    yield
    deps._revoked_redis = None
    deps._revoked_redis_checked = False


@pytest.fixture(autouse=True)
def _clear_file_blacklist():
    """清空 in-memory + file blacklist,避免測試間污染。"""
    with deps._revoked_tokens_lock:
        deps._revoked_tokens.clear()


# ──────────────────────────────────────────────────────────────────────────────
# Degrade 路徑(無 Redis)— 應完全退回 file-backed
# ──────────────────────────────────────────────────────────────────────────────


def test_revoke_without_redis_uses_file():
    """無 Redis 時,revoke 應寫入 file-backed blacklist。"""
    with patch.object(deps, "_get_revoked_redis_client", return_value=None):
        deps.revoke_token("token-no-redis-1")
        assert deps.is_token_revoked("token-no-redis-1") is True


def test_is_revoked_false_for_unrevoked_without_redis():
    """無 Redis 時,未 revoke 的 token → False。"""
    with patch.object(deps, "_get_revoked_redis_client", return_value=None):
        assert deps.is_token_revoked("never-revoked-token") is False


def test_revoke_then_check_consistent_without_redis():
    """無 Redis:revoke 後 is_revoked 一致(模擬 multi-call)。"""
    with patch.object(deps, "_get_revoked_redis_client", return_value=None):
        deps.revoke_token("consistent-token")
        assert deps.is_token_revoked("consistent-token") is True
        assert deps.is_token_revoked("consistent-token") is True  # 第二次查仍 True


# ──────────────────────────────────────────────────────────────────────────────
# Redis 路徑 — 應走 Redis(跨 worker)
# ──────────────────────────────────────────────────────────────────────────────


def test_revoke_with_redis_uses_redis():
    """有 Redis 時,revoke 應寫入 Redis(SET with TTL)。"""
    mock_redis = MagicMock()
    with patch.object(deps, "_get_revoked_redis_client", return_value=mock_redis):
        deps.revoke_token("token-redis-1")
    # 確認 Redis SET 被呼叫(key 含 hash 前綴)
    assert mock_redis.set.called
    call_args = mock_redis.set.call_args
    assert call_args[0][0].startswith("revoked_token:")
    assert call_args[1].get("ex") > 0  # TTL 設了


def test_is_revoked_true_when_redis_has_key():
    """Redis 有該 key → is_revoked True。"""
    mock_redis = MagicMock()
    mock_redis.exists.return_value = 1
    with patch.object(deps, "_get_revoked_redis_client", return_value=mock_redis):
        assert deps.is_token_revoked("revoked-in-redis") is True
    mock_redis.exists.assert_called_once()


def test_is_revoked_false_when_redis_empty():
    """Redis 沒該 key → is_revoked False。"""
    mock_redis = MagicMock()
    mock_redis.exists.return_value = 0
    with patch.object(deps, "_get_revoked_redis_client", return_value=mock_redis):
        assert deps.is_token_revoked("not-in-redis") is False


# ──────────────────────────────────────────────────────────────────────────────
# Redis 故障 → 自動 degrade(不該讓 auth 壞掉)
# ──────────────────────────────────────────────────────────────────────────────


def test_redis_failure_on_revoke_falls_back_to_file():
    """Redis SET 拋例外 → revoke 退回 file(不該傳播)。"""
    mock_redis = MagicMock()
    mock_redis.set.side_effect = Exception("Redis down")
    with patch.object(deps, "_get_revoked_redis_client", return_value=mock_redis):
        deps.revoke_token("token-redis-fail")
    # 退回 file → is_revoked 仍應 True
    with patch.object(deps, "_get_revoked_redis_client", return_value=mock_redis):
        # Redis exists 也壞了會退 file
        mock_redis.exists.side_effect = Exception("Redis down")
        assert deps.is_token_revoked("token-redis-fail") is True


def test_redis_failure_on_check_falls_back_to_file():
    """Redis EXISTS 拋例外 → is_revoked 退 file(寧可誤擋不放行?不,file 無則 False)。"""
    mock_redis = MagicMock()
    mock_redis.exists.side_effect = Exception("Redis down")
    # 先用 file revoke
    with patch.object(deps, "_get_revoked_redis_client", return_value=None):
        deps.revoke_token("token-check-fail")
    # 檢查時 Redis 壞 → 退 file → file 有 → True
    with patch.object(deps, "_get_revoked_redis_client", return_value=mock_redis):
        assert deps.is_token_revoked("token-check-fail") is True


# ──────────────────────────────────────────────────────────────────────────────
# _get_revoked_redis_client — lazy + cached
# ──────────────────────────────────────────────────────────────────────────────


def test_redis_client_no_env_returns_none(monkeypatch):
    """未設 REDIS_URL/REDIS_HOST → None(degrade)。"""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    deps._revoked_redis = None
    deps._revoked_redis_checked = False
    assert deps._get_revoked_redis_client() is None


def test_redis_client_cached_after_first_check():
    """第一次檢查後 cached(_checked flag),第二次不重試。"""
    # 設 _checked=True 後應直接回 cached 值,不重試連線
    deps._revoked_redis_checked = True
    deps._revoked_redis = None  # cached 為 None
    assert deps._get_revoked_redis_client() is None


# ──────────────────────────────────────────────────────────────────────────────
# 過期 token 清理(file 路徑既有行為不變)
# ──────────────────────────────────────────────────────────────────────────────


def test_expired_token_not_revoked():
    """過期的 revoke 條目 → is_revoked False(已過期 = 無效)。"""
    past_expiry = datetime.now(timezone.utc) - timedelta(days=1)
    with patch.object(deps, "_get_revoked_redis_client", return_value=None):
        # 直接寫入過期條目
        token_hash = deps._hash_token("expired-token")
        with deps._revoked_tokens_lock:
            deps._revoked_tokens[token_hash] = past_expiry.isoformat()
        assert deps.is_token_revoked("expired-token") is False
