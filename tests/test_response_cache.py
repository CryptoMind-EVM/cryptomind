"""Tests for core.agents.response_cache（L5）。

最重要的契約是「只快取沒有呼叫工具的回覆」——在投資情境給過期報價是信任
問題，不是效能問題。TestNeverCacheMarketData 那組若有漏網就是線上事故。

其次是使用者隔離：回覆可能帶暱稱、記憶脈絡、錢包上下文，不可跨使用者共享。
"""

from __future__ import annotations

import time

import pytest

from core.agents import response_cache as rc

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_cache():
    rc.clear()
    yield
    rc.clear()


class TestNeverCacheMarketData:
    """有呼叫工具 = 含即時資料 = 絕不可快取。"""

    @pytest.mark.parametrize("tool_calls", [1, 2, 10])
    def test_refuses_to_store_when_tools_were_used(self, tool_calls):
        stored = rc.put(
            "u1", "BTC 現在多少錢", "zh-TW", "約 $50,000", tool_calls=tool_calls
        )
        assert stored is False
        assert rc.get("u1", "BTC 現在多少錢", "zh-TW") is None

    def test_tool_free_answer_is_cacheable(self):
        assert rc.put("u1", "什麼是 RSI", "zh-TW", "RSI 是...", tool_calls=0) is True
        assert rc.get("u1", "什麼是 RSI", "zh-TW") == "RSI 是..."


class TestUserIsolation:
    def test_other_user_never_sees_cached_answer(self):
        rc.put("u1", "你能做什麼", "zh-TW", "我是 u1 的專屬回覆", tool_calls=0)
        assert rc.get("u2", "你能做什麼", "zh-TW") is None

    def test_anonymous_is_its_own_bucket(self):
        rc.put(None, "你能做什麼", "zh-TW", "匿名回覆", tool_calls=0)
        assert rc.get("u1", "你能做什麼", "zh-TW") is None
        assert rc.get(None, "你能做什麼", "zh-TW") == "匿名回覆"

    def test_invalidate_user_only_clears_that_user(self):
        rc.put("u1", "q", "zh-TW", "a1", tool_calls=0)
        rc.put("u2", "q", "zh-TW", "a2", tool_calls=0)
        assert rc.invalidate_user("u1") == 1
        assert rc.get("u1", "q", "zh-TW") is None
        assert rc.get("u2", "q", "zh-TW") == "a2"


class TestKeyScoping:
    def test_language_is_part_of_the_key(self):
        """同一句話在不同語系要有不同回覆，不可互相命中。"""
        rc.put("u1", "help", "zh-TW", "繁中回覆", tool_calls=0)
        assert rc.get("u1", "help", "en") is None
        assert rc.get("u1", "help", "zh-TW") == "繁中回覆"

    @pytest.mark.parametrize(
        "variant", ["什麼是 RSI", "  什麼是 RSI  ", "什麼是 RSI！", "什麼是 RSI。"]
    )
    def test_normalization_makes_variants_hit(self, variant):
        rc.put("u1", "什麼是 RSI", "zh-TW", "RSI 是...", tool_calls=0)
        assert rc.get("u1", variant, "zh-TW") == "RSI 是..."

    def test_different_questions_do_not_collide(self):
        """完整比對，不是模糊相似度——BTC 的答案不可配給 ETH 的問題。"""
        rc.put("u1", "什麼是 RSI", "zh-TW", "RSI 答案", tool_calls=0)
        assert rc.get("u1", "什麼是 MACD", "zh-TW") is None


class TestEmptyAndExpiry:
    @pytest.mark.parametrize("query", ["", "   "])
    def test_empty_query_not_stored_or_read(self, query):
        assert rc.put("u1", query, "zh-TW", "x", tool_calls=0) is False
        assert rc.get("u1", query, "zh-TW") is None

    @pytest.mark.parametrize("response", ["", "   "])
    def test_empty_response_not_stored(self, response):
        assert rc.put("u1", "q", "zh-TW", response, tool_calls=0) is False

    def test_expired_entry_is_a_miss(self, monkeypatch):
        rc.put("u1", "q", "zh-TW", "a", tool_calls=0)
        assert rc.get("u1", "q", "zh-TW") == "a"
        # 把時鐘推過 TTL。基準必須是 time.time()（epoch）——快取存的就是它，
        # 用 monotonic 當基準會得到一個遠早於 epoch 的「未來」。
        future = time.time() + rc.TTL_SECONDS + 10
        monkeypatch.setattr(time, "time", lambda: future)
        assert rc.get("u1", "q", "zh-TW") is None


class TestBounds:
    def test_cache_is_bounded(self, monkeypatch):
        monkeypatch.setattr(rc, "MAX_ENTRIES", 5)
        for i in range(20):
            rc.put("u1", f"question {i}", "zh-TW", f"answer {i}", tool_calls=0)
        assert len(rc._cache) <= 5

    def test_eviction_is_oldest_first(self, monkeypatch):
        monkeypatch.setattr(rc, "MAX_ENTRIES", 3)
        for i in range(3):
            rc.put("u1", f"q{i}", "zh-TW", f"a{i}", tool_calls=0)
        rc.put("u1", "q9", "zh-TW", "a9", tool_calls=0)
        assert rc.get("u1", "q0", "zh-TW") is None  # 最舊的被踢掉
        assert rc.get("u1", "q9", "zh-TW") == "a9"
