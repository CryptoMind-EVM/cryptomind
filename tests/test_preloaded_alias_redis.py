"""Preloaded ticker alias Redis 遷移測試。

驗證 preloaded alias（~11000 個 TW/US 公司名）改存 Redis HASH 後的行為：
- lookup 先查 Redis，miss 才 fallback 到 in-memory dict
- Redis 不可用時優雅降級（不壞）
- add_preloaded_aliases 同步寫 Redis
- preloader fast-path：Redis 已有時跳過下載

測試用 monkeypatch fake Redis client（不依賴真 Redis），對齊 test_shared_cache 模式。
"""

from __future__ import annotations

import pytest

from core.tools import multi_market_resolver as mmr

# ── Fake Redis client（模擬 redis-py 的 HGET/HSET/EXISTS/EXPIRE 等）─────────


class _FakeRedis:
    """最小可用 fake Redis，只實作 alias 遷移會用到的指令。

    模擬 redis-py decode_responses=False：key/field 傳入時自動轉 bytes（真實
    redis-py 也這樣處理 str key），value 維持原樣。
    """

    def __init__(self):
        self._hashes: dict[bytes, dict[bytes, bytes]] = {}
        self._ttls: dict[bytes, float] = {}
        self.calls: list[tuple] = []

    @staticmethod
    def _b(v):
        return v.encode() if isinstance(v, str) else v

    def hget(self, key, field):
        kb, fb = self._b(key), self._b(field)
        self.calls.append(("hget", kb, fb))
        return self._hashes.get(kb, {}).get(fb)

    def exists(self, key):
        kb = self._b(key)
        self.calls.append(("exists", kb))
        return 1 if kb in self._hashes else 0

    def hlen(self, key):
        kb = self._b(key)
        self.calls.append(("hlen", kb))
        return len(self._hashes.get(kb, {}))

    def expire(self, key, ttl):
        kb = self._b(key)
        self.calls.append(("expire", kb, ttl))
        self._ttls[kb] = ttl
        return 1

    def pipeline(self):
        return _FakePipeline(self)

    def ping(self):
        return True


class _FakePipeline:
    def __init__(self, redis: _FakeRedis):
        self._redis = redis
        self._cmds: list[tuple] = []

    def hset(self, key, mapping=None):
        self._cmds.append(("hset", key, mapping))

    def expire(self, key, ttl):
        self._cmds.append(("expire", key, ttl))

    def execute(self):
        for cmd in self._cmds:
            self._redis.calls.append(cmd)
            if cmd[0] == "hset":
                _, key, mapping = cmd
                kb = self._redis._b(key)
                store = self._redis._hashes.setdefault(kb, {})
                for field, value in (mapping or {}).items():
                    store[self._redis._b(field)] = value
            elif cmd[0] == "expire":
                self._redis._ttls[self._redis._b(cmd[1])] = cmd[2]
        return [True] * len(self._cmds)


@pytest.fixture(autouse=True)
def _reset_alias_state(monkeypatch):
    """每個測試前重置 alias 相關 module-level state。"""
    mmr._LOCAL_ALIASES = None
    mmr._reset_alias_redis_client()
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    yield
    mmr._LOCAL_ALIASES = None
    mmr._reset_alias_redis_client()


# ── 1. lookup 先查 Redis ----------------------------------------------------


def test_lookup_uses_redis_when_populated(monkeypatch):
    """Redis 有 alias 時，_lookup_local_alias 應從 Redis 拿。"""
    fake = _FakeRedis()
    fake._hashes[fake._b(mmr._ALIAS_REDIS_KEY)] = {
        fake._b("apple inc."): b'{"symbol":"AAPL","market":"us","name":"Apple Inc."}'
    }
    monkeypatch.setattr(mmr, "_get_alias_redis_client", lambda: fake)

    result = mmr._lookup_local_alias("Apple Inc.")

    assert result is not None
    assert result.symbol == "AAPL"
    assert result.market == "us"
    # 確認有呼叫 hget
    assert any(c[0] == "hget" for c in fake.calls)


def test_lookup_falls_back_to_inmemory_on_redis_miss(monkeypatch):
    """Redis miss 時 fallback 到 in-memory dict（curated/learned）。"""
    fake = _FakeRedis()  # 空 Redis
    monkeypatch.setattr(mmr, "_get_alias_redis_client", lambda: fake)
    # 手動塞 in-memory（模擬 curated）
    mmr._LOCAL_ALIASES = {"台積電": {"symbol": "2330", "market": "tw", "name": "台積電"}}

    result = mmr._lookup_local_alias("台積電")

    assert result is not None
    assert result.symbol == "2330"


def test_lookup_falls_back_when_redis_unavailable(monkeypatch):
    """Redis 不通時（client=None），正常 fallback 到 in-memory 不壞。"""
    monkeypatch.setattr(mmr, "_get_alias_redis_client", lambda: None)
    mmr._LOCAL_ALIASES = {"鴻海": {"symbol": "2317", "market": "tw", "name": "鴻海"}}

    result = mmr._lookup_local_alias("鴻海")

    assert result is not None
    assert result.symbol == "2317"


def test_lookup_returns_none_when_both_miss(monkeypatch):
    """Redis miss 且 in-memory 也 miss → 回 None。"""
    fake = _FakeRedis()
    monkeypatch.setattr(mmr, "_get_alias_redis_client", lambda: fake)
    mmr._LOCAL_ALIASES = {}

    result = mmr._lookup_local_alias("不存在的標的")
    assert result is None


# ── 2. add_preloaded_aliases 寫 Redis ---------------------------------------


def test_add_preloaded_writes_to_redis(monkeypatch):
    """add_preloaded_aliases 同時寫 in-memory 和 Redis HASH（pipeline）。"""
    fake = _FakeRedis()
    monkeypatch.setattr(mmr, "_get_alias_redis_client", lambda: fake)

    entries = {
        "apple inc.": {"symbol": "AAPL", "market": "us", "name": "Apple Inc."},
        "microsoft": {"symbol": "MSFT", "market": "us", "name": "Microsoft"},
    }
    mmr.add_preloaded_aliases(entries)

    # 確認 Redis 有被寫入
    assert any(c[0] == "hset" for c in fake.calls)
    stored = fake._hashes[fake._b(mmr._ALIAS_REDIS_KEY)]
    assert fake._b("apple inc.") in stored
    assert fake._b("microsoft") in stored
    # 確認有設 TTL
    assert any(c[0] == "expire" for c in fake.calls)


def test_add_preloaded_skips_redis_when_unavailable(monkeypatch):
    """Redis 不通時，add_preloaded_aliases 只寫 in-memory，不丟例外。"""
    monkeypatch.setattr(mmr, "_get_alias_redis_client", lambda: None)

    entries = {"test": {"symbol": "TST", "market": "us", "name": "Test"}}
    added = mmr.add_preloaded_aliases(entries)

    assert added == 1  # in-memory 有寫入
    # 不該丟例外


# ── 3. preloader fast-path --------------------------------------------------


def test_alias_redis_exists_true_when_populated(monkeypatch):
    """Redis HASH 有資料時，_alias_redis_exists 回 True。"""
    fake = _FakeRedis()
    fake._hashes[fake._b(mmr._ALIAS_REDIS_KEY)] = {fake._b("x"): b"y"}
    monkeypatch.setattr(mmr, "_get_alias_redis_client", lambda: fake)

    assert mmr._alias_redis_exists() is True


def test_alias_redis_exists_false_when_empty(monkeypatch):
    """Redis HASH 空時，_alias_redis_exists 回 False。"""
    fake = _FakeRedis()
    monkeypatch.setattr(mmr, "_get_alias_redis_client", lambda: fake)

    assert mmr._alias_redis_exists() is False


def test_alias_redis_exists_false_when_unavailable(monkeypatch):
    """Redis 不通時，_alias_redis_exists 回 False（會觸發下載）。"""
    monkeypatch.setattr(mmr, "_get_alias_redis_client", lambda: None)

    assert mmr._alias_redis_exists() is False
