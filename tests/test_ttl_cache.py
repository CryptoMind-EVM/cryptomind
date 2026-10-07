"""api/routers/_ttl_cache.TTLCache：8 個行情路由共用的快取（原本各抄一份、沒有上限）。"""

from __future__ import annotations

import pytest

from api.routers._ttl_cache import TTLCache

pytestmark = pytest.mark.unit


def test_hit_miss_and_expiry():
    c = TTLCache(default_ttl=300)
    assert c.get("k") is None
    c.set("k", {"v": 1})
    assert c.get("k") == {"v": 1}
    c.set("gone", 1, ttl=0)
    assert c.get("gone") is None


def test_bounded_drops_expired_then_oldest():
    c = TTLCache(default_ttl=300, max_entries=3)
    c.set("expired", 1, ttl=0)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # 超過上限 → 先清過期的
    assert len(c) == 3 and c.get("a") == 1
    c.set("d", 4)  # 沒有過期的可清 → 丟最早放進來的 a
    assert len(c) == 3 and c.get("a") is None and c.get("d") == 4


@pytest.mark.parametrize(
    "module, ttl",
    [("commodity", 600), ("forex", 60), ("usstock", 300), ("krstock", 300)],
)
def test_routers_keep_their_default_ttl(module, ttl):
    import importlib

    mod = importlib.import_module(f"api.routers.{module}")
    assert mod._cache.default_ttl == ttl
    assert mod._get_cache == mod._cache.get and mod._set_cache == mod._cache.set
