"""bootstrap._manager_cache 的 invalidate 行為測試。

特別覆蓋一個既有 bug：invalidate_manager_cache(user_id, session_id) 過去用
_manager_cache_key(user_id, session_id) 組單一 key（fingerprint/language 為空），
但 production 的 cache key 含真實 key_fingerprint（sha256 前 8 碼）+ language，
所以這個 invalidate 對「特定 session_id」永遠 pop 不到。修正後改用 prefix 搜。
"""

from __future__ import annotations

import importlib

# core.agents.__init__ 把 bootstrap 函式蓋過 module 名字，用 importlib 抓 module
bootstrap = importlib.import_module("core.agents.bootstrap")


def _seed_cache(user_id, session_id, fingerprint, language):
    """建一個假的 cache entry（用 sentinel 物件代表 manager，省去建構成本）。"""
    key = bootstrap._manager_cache_key(user_id, session_id, fingerprint, language)
    bootstrap._manager_cache[key] = (object(), 0.0)  # (manager, created_at=now 過期用 0)
    return key


def test_invalidate_specific_session_works_across_fingerprints(monkeypatch):
    """同 user + session、不同 fingerprint/language 的多個 manager 都要被清掉。

    這是既有 bug 的 regression 測試：修正前只會 pop fingerprint/language 都空
    的那個 key（不存在），實際 entry 全部殘留。
    """
    # 清空 cache 確保起點乾淨
    bootstrap._manager_cache.clear()

    user_id = "u1"
    session_id = "sess-old"
    _seed_cache(user_id, session_id, fingerprint="a1b2c3d4", language="zh-TW")
    _seed_cache(user_id, session_id, fingerprint="e5f6g7h8", language="en")
    # 另一個 session 不該被影響
    other_key = _seed_cache(user_id, "sess-other", fingerprint="a1b2c3d4", language="zh-TW")

    bootstrap.invalidate_manager_cache(user_id, session_id)

    keys = list(bootstrap._manager_cache.keys())
    # sess-old 的兩個 entry 都該被清掉
    assert not any(f":{session_id}:" in k for k in keys), (
        f"sess-old entries still in cache: {[k for k in keys if f':{session_id}:' in k]}"
    )
    # sess-other 必須保留
    assert other_key in bootstrap._manager_cache, "不該誤刪其他 session"


def test_invalidate_without_session_clears_all_for_user():
    """session_id=None 時清掉該 user 的所有 entry（既有行為，不能 regress）。"""
    bootstrap._manager_cache.clear()

    _seed_cache("u1", "s1", "fp1", "zh-TW")
    _seed_cache("u1", "s2", "fp2", "en")
    _seed_cache("u2", "s1", "fp1", "zh-TW")  # 其他 user 不動

    bootstrap.invalidate_manager_cache("u1")

    keys = list(bootstrap._manager_cache.keys())
    assert not any(k.startswith("u1:") for k in keys), "u1 的 entry 應全清"
    assert any(k.startswith("u2:") for k in keys), "u2 不該被影響"


def test_invalidate_nonexistent_session_is_noop():
    """invalidate 不存在的 session 不該報錯。"""
    bootstrap._manager_cache.clear()
    _seed_cache("u1", "s1", "fp1", "zh-TW")

    # 不會 raise
    bootstrap.invalidate_manager_cache("u1", "does-not-exist")
    assert len(bootstrap._manager_cache) == 1


# ── 執行緒安全：bootstrap 走 run_sync 執行緒池，多個請求同時讀寫 _manager_cache ──
# 沒鎖時 get_manager_instances 迭代中被別的執行緒改到會炸
# 「OrderedDict mutated during iteration」，刪同一個 key 兩次會 KeyError。
# 這裡驗「每個入口都真的走同一把鎖」：測試先拿著鎖，入口必須卡住等。

import threading  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402


def _blocks_on_cache_lock(fn) -> None:
    done = threading.Event()

    def _call():
        fn()
        done.set()

    with bootstrap._manager_cache_lock:
        worker = threading.Thread(target=_call, daemon=True)
        worker.start()
        assert not done.wait(0.2), "拿著快取鎖時仍能讀寫 _manager_cache——沒走鎖"
    assert done.wait(5), "放開鎖後沒有完成"
    worker.join(5)


@pytest.mark.parametrize(
    "call",
    [
        lambda: bootstrap.invalidate_manager_cache("u1"),
        lambda: bootstrap.get_manager_instances("u1"),
        lambda: bootstrap.get_manager_instance("u1", "s1"),
    ],
    ids=["invalidate", "get_instances", "get_instance"],
)
def test_cache_helpers_take_the_lock(call):
    bootstrap._manager_cache.clear()
    _seed_cache("u1", "s1", "fp1", "zh-TW")
    _blocks_on_cache_lock(call)


def test_bootstrap_cache_hit_takes_the_lock():
    """bootstrap 的 cache-hit 查找（get／move_to_end）也要在鎖內。"""
    bootstrap._manager_cache.clear()
    key = bootstrap._manager_cache_key("u1", "s1", "fp1", "zh-TW")
    cached = SimpleNamespace(
        session_id="s1",
        agent_registry=SimpleNamespace(_agents={}),
    )
    bootstrap._manager_cache[key] = (cached, bootstrap.time.time())

    result = {}

    def _call():
        result["manager"] = bootstrap.bootstrap(
            SimpleNamespace(),
            language="zh-TW",
            user_id="u1",
            session_id="s1",
            key_fingerprint="fp1",
        )

    _blocks_on_cache_lock(_call)
    assert result["manager"] is cached
    bootstrap._manager_cache.clear()
