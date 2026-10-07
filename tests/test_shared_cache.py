"""
test_shared_cache.py — 共用快取（Redis L2）與 base_provider 兩層快取行為

涵蓋：
- shared_cache 在無 Redis 時優雅降級為 no-op（不丟例外）
- base_provider L1 in-process 快取仍正常（TTL 命中 / 過期）
- L1 miss 時會查 L2，命中後回填 L1
- _cache_set 會同時寫 L1 與 L2
- L2 key 有 market namespace

執行：
    pytest tests/test_shared_cache.py
"""

from __future__ import annotations

import time

import pytest

from core import shared_cache
from core.providers.base_provider import StockDataProvider


class _DummyProvider(StockDataProvider):
    """最小可實例化 Provider（只為測快取，不抓真資料）。"""

    market = "us"

    def get_price(self, symbol):  # pragma: no cover - 不會被呼叫
        return {}

    def get_technicals(self, symbol):  # pragma: no cover
        return {}

    def get_fundamentals(self, symbol):  # pragma: no cover
        return {}


@pytest.fixture(autouse=True)
def _reset_shared_cache():
    shared_cache.reset()
    yield
    shared_cache.reset()


@pytest.mark.unit
class TestSharedCacheDegradation:
    def test_no_redis_get_returns_none(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.delenv("REDIS_HOST", raising=False)
        shared_cache.reset()
        assert shared_cache.get_json("md:us:price:AAPL") is None

    def test_no_redis_set_is_noop(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.delenv("REDIS_HOST", raising=False)
        shared_cache.reset()
        # 不應丟例外
        shared_cache.set_json("md:us:price:AAPL", {"price": 1}, ttl=10)

    def test_memory_scheme_disables_l2(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "memory://")
        shared_cache.reset()
        assert shared_cache.get_json("k") is None


@pytest.mark.unit
class TestBaseProviderL1:
    def test_l1_hit_within_ttl(self):
        p = _DummyProvider()
        p._cache_set("price:AAPL", {"price": 10}, ttl=60)
        assert p._cache_get("price:AAPL") == {"price": 10}

    def test_l1_expires(self):
        p = _DummyProvider()
        p._cache_set("price:AAPL", {"price": 10}, ttl=1)
        time.sleep(1.1)
        assert p._cache_get("price:AAPL") is None

    def test_l2_namespaced_key(self):
        p = _DummyProvider()
        assert p._l2_key("price:AAPL") == "md:us:price:AAPL"


@pytest.mark.unit
class TestTwoTierInteraction:
    def test_l1_miss_falls_back_to_l2_and_backfills(self, monkeypatch):
        """L1 沒有時查 L2，命中後回填 L1。"""
        store: dict = {}
        monkeypatch.setattr(shared_cache, "get_json", lambda k: store.get(k))
        monkeypatch.setattr(
            shared_cache, "set_json", lambda k, v, ttl: store.__setitem__(k, v)
        )

        p = _DummyProvider()
        # 模擬另一個 worker 已寫進 L2
        store["md:us:price:AAPL"] = {"price": 99}

        # L1 沒有 → 應從 L2 取得
        assert p._cache_get("price:AAPL") == {"price": 99}
        # 並回填 L1（直接看 L1）
        assert p._l1_get("price:AAPL") == {"price": 99}

    def test_set_writes_both_tiers(self, monkeypatch):
        store: dict = {}
        monkeypatch.setattr(shared_cache, "get_json", lambda k: store.get(k))
        monkeypatch.setattr(
            shared_cache, "set_json", lambda k, v, ttl: store.__setitem__(k, v)
        )

        p = _DummyProvider()
        p._cache_set("technicals:AAPL", {"rsi": 55}, ttl=60)

        assert p._l1_get("technicals:AAPL") == {"rsi": 55}
        assert store["md:us:technicals:AAPL"] == {"rsi": 55}
