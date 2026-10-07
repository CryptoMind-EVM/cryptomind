"""GET /api/user/me 30s per-user TTL 快取測試（2026-08-12 timeout 修復）。

/me 被前端高頻呼叫（page-load + i18n 三事件 fan-out），每次 2 個 run_sync 搶
shared DB executor；Zeabur postgres 偶發慢時 executor 被佔死 → timeout 500。
修復：module-level TTLCache，同一 user 30s 內第二次呼叫直接回快取不打 DB；
setter（語言/暱稱/LLM provider）主動 invalidate。

用 monkeypatch 假造 repo（不打真 DB）。
"""


async def _patch_repos(router, monkeypatch, calls):
    async def fake_lang(uid, session=None):
        calls["lang"] = calls.get("lang", 0) + 1
        return "zh-TW"

    async def fake_disp(uid, session=None):
        calls["disp"] = calls.get("disp", 0) + 1
        return "Danny"

    async def fake_prov(uid, session=None):
        calls["prov"] = calls.get("prov", 0) + 1
        return None

    monkeypatch.setattr(router.user_repo, "get_language", fake_lang)
    monkeypatch.setattr(router.user_repo, "get_display_name", fake_disp)
    monkeypatch.setattr(
        router.user_llm_preferences_repo, "get_selected_provider", fake_prov
    )


async def test_me_cache_hit_skips_db(monkeypatch):
    from api.routers import user as router

    router._ME_CACHE.clear()
    calls: dict = {}
    await _patch_repos(router, monkeypatch, calls)
    current_user = {
        "user_id": "u1",
        "username": "ton_u1",
        "auth_method": "ton_wallet",
    }
    r1 = await router.get_current_user_profile(current_user)
    r2 = await router.get_current_user_profile(current_user)

    assert r1 == r2
    assert r1["user"]["language"] == "zh-TW"
    assert r1["user"]["display_name"] == "Danny"
    # 第二次命中快取 → 三個 repo 都只被打一次
    assert calls == {"lang": 1, "disp": 1, "prov": 1}


async def test_me_cache_separate_per_user(monkeypatch):
    from api.routers import user as router

    router._ME_CACHE.clear()
    calls: dict = {}
    await _patch_repos(router, monkeypatch, calls)

    await router.get_current_user_profile(
        {"user_id": "u1", "username": "a", "auth_method": "ton_wallet"}
    )
    await router.get_current_user_profile(
        {"user_id": "u2", "username": "b", "auth_method": "ton_wallet"}
    )
    # 不同 user 各自 miss → 各打一次 DB
    assert calls == {"lang": 2, "disp": 2, "prov": 2}


async def test_me_cache_is_ttlcache_with_30s_ttl():
    from cachetools import TTLCache

    from api.routers import user as router

    assert isinstance(router._ME_CACHE, TTLCache)
    assert router._ME_CACHE.ttl == 30


async def test_me_cache_invalidated_by_setter_pop(monkeypatch):
    """setter 用 _ME_CACHE.pop(user_id) invalidate；這裡驗證 pop 後下次會 miss。"""
    from api.routers import user as router

    router._ME_CACHE.clear()
    calls: dict = {}
    await _patch_repos(router, monkeypatch, calls)
    cu = {"user_id": "u1", "username": "a", "auth_method": "ton_wallet"}

    await router.get_current_user_profile(cu)
    assert calls == {"lang": 1, "disp": 1, "prov": 1}
    # 模擬 setter invalidate
    router._ME_CACHE.pop("u1", None)
    await router.get_current_user_profile(cu)
    # pop 後重新 miss → 再打一次 DB
    assert calls == {"lang": 2, "disp": 2, "prov": 2}
