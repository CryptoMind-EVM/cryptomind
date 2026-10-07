"""Tests for POST /api/chat/greeting — LLM 動態生成的個人化招呼語。

設計契約：
- 認證後讀 current_user 的 display_name（fallback username）+ wallet_address
- 用輕量 LLM（小模型）生成一句招呼（~30 字）
- 以 (user_id, session_id, language) 為 key 做in-memory快取，同 session 重整不重扣
- 未認證 → 401；LLM 失敗 → fallback 靜態招呼（不讓使用者看到 500）
"""

from __future__ import annotations

import pytest

from api.routers import analysis as analysis_router

# ============================================================================
# 成功路徑 + 快取
# ============================================================================


@pytest.mark.asyncio
class TestChatGreetingEndpoint:
    @pytest.fixture(autouse=True)
    def _reset_greeting_state(self):
        """每個 test 前：(1) 清空 greeting cache（session + user-recent）避免跨 test 命中；
        (2) 重置 in-memory rate limiter 計數器，避免 3/min 限流干擾整合測試。
        限流本身有 test_middleware_rate_limit.py 專門覆蓋，這裡聚焦 greeting 行為。
        """
        analysis_router._GREETING_CACHE.clear()
        analysis_router._GREETING_USER_RECENT.clear()
        try:
            analysis_router.limiter.reset()
        except Exception:  # noqa: BLE001
            # 某些 storage backend 不支援 reset；忽略（memory:// 支援）
            pass
        yield
        analysis_router._GREETING_CACHE.clear()
        analysis_router._GREETING_USER_RECENT.clear()

    async def test_returns_greeting_text(self, client, auth_headers, monkeypatch):
        captured = {}

        async def fake_generate(*, display_name, wallet_address, language, tier, current_user=None):
            captured["display_name"] = display_name
            captured["language"] = language
            return f"您好 {display_name}，我是 CryptoMind 小幫手，今天有什麼事？"

        monkeypatch.setattr(analysis_router, "_generate_greeting", fake_generate)

        response = await client.post(
            "/api/chat/greeting",
            json={"session_id": "sess-001", "language": "zh-TW"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert "您好" in body["greeting"]
        assert body["cached"] is False
        assert captured["language"] == "zh-TW"

    async def test_same_session_uses_cache_on_second_call(
        self, client, auth_headers, monkeypatch
    ):
        call_count = {"n": 0}

        async def fake_generate(**kwargs):
            call_count["n"] += 1
            return "cached greeting"

        monkeypatch.setattr(analysis_router, "_generate_greeting", fake_generate)

        # 第一次呼叫 → 生成
        r1 = await client.post(
            "/api/chat/greeting",
            json={"session_id": "sess-cache", "language": "zh-TW"},
            headers=auth_headers,
        )
        assert r1.status_code == 200
        assert r1.json()["cached"] is False
        assert call_count["n"] == 1

        # 同 session + 同 language 第二次 → 走快取
        r2 = await client.post(
            "/api/chat/greeting",
            json={"session_id": "sess-cache", "language": "zh-TW"},
            headers=auth_headers,
        )
        assert r2.status_code == 200
        assert r2.json()["cached"] is True
        assert call_count["n"] == 1  # 沒再呼叫 LLM

    async def test_different_session_reuses_user_level_cache(
        self, client, auth_headers, monkeypatch
    ):
        """#7 成本 DoS 防護：同 user 換 session_id（同 language）→ 命中 user-level
        短窗 cache，只扣 1 次 server LLM。攻擊者循環 session_id 無法燒額度。
        """
        call_count = {"n": 0}

        async def fake_generate(**kwargs):
            call_count["n"] += 1
            return "greeting"

        monkeypatch.setattr(analysis_router, "_generate_greeting", fake_generate)

        resp_a = await client.post(
            "/api/chat/greeting",
            json={"session_id": "sess-A", "language": "zh-TW"},
            headers=auth_headers,
        )
        resp_b = await client.post(
            "/api/chat/greeting",
            json={"session_id": "sess-B", "language": "zh-TW"},
            headers=auth_headers,
        )

        # 只扣 1 次 LLM（第二次命中 user-level cache）
        assert call_count["n"] == 1
        assert resp_a.json()["cached"] is False
        assert resp_b.json()["cached"] is True
        # 兩次回同一句（重用 user-recent）
        assert resp_a.json()["greeting"] == resp_b.json()["greeting"]

    async def test_language_change_bypasses_cache(
        self, client, auth_headers, monkeypatch
    ):
        """同 session 但語言切換 → 應重新生成（不同語言的招呼）。"""
        call_count = {"n": 0}

        async def fake_generate(**kwargs):
            call_count["n"] += 1
            return "greeting"

        monkeypatch.setattr(analysis_router, "_generate_greeting", fake_generate)

        await client.post(
            "/api/chat/greeting",
            json={"session_id": "sess-lang", "language": "zh-TW"},
            headers=auth_headers,
        )
        await client.post(
            "/api/chat/greeting",
            json={"session_id": "sess-lang", "language": "en"},
            headers=auth_headers,
        )

        assert call_count["n"] == 2

    async def test_llm_failure_falls_back_to_static_greeting(
        self, client, auth_headers, monkeypatch
    ):
        """LLM 掛掉時不可讓使用者看到 500；fallback 一句靜態招呼。"""

        async def fake_generate(**kwargs):
            raise RuntimeError("LLM provider down")

        monkeypatch.setattr(analysis_router, "_generate_greeting", fake_generate)

        response = await client.post(
            "/api/chat/greeting",
            json={"session_id": "sess-fail", "language": "zh-TW"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert len(body["greeting"]) > 0
        assert body["cached"] is False
        # fallback 標記（讓前端知道不是 LLM 生成，可選擇重試）
        assert body.get("fallback") is True

    async def test_passes_display_name_to_generator(
        self, client, auth_headers, monkeypatch
    ):
        """確認 current_user 的 username/display_name 有傳到 generator。"""
        captured = {}

        async def fake_generate(*, display_name, wallet_address, language, tier, current_user=None):
            captured["display_name"] = display_name
            captured["wallet_address"] = wallet_address
            captured["tier"] = tier
            return "greeting"

        monkeypatch.setattr(analysis_router, "_generate_greeting", fake_generate)

        await client.post(
            "/api/chat/greeting",
            json={"session_id": "sess-ctx", "language": "zh-TW"},
            headers=auth_headers,
        )

        # TEST_MODE 預設 user=test-user-001，username=TestUser_001
        assert captured["display_name"]  # 有值
        assert captured["tier"]  # 有值


# ============================================================================
# 招呼語走 BYOK（與 chat 分析一致）— server key 不再使用
# ============================================================================


@pytest.mark.unit
async def test_generate_greeting_uses_user_byok(monkeypatch):
    """_generate_greeting 應走使用者 BYOK（resolve_user_llm_credentials），而非 server key。"""
    from api.routers import analysis as ar

    resolved = {"provider": "openai", "api_key": "sk-user-key", "model": "gpt-4o-mini"}

    async def fake_resolve(current_user, preferred_provider=None):
        return resolved

    created = {}

    def fake_create(*, provider, api_key, model, **kwargs):
        created["provider"] = provider
        created["api_key"] = api_key
        created["model"] = model

        class _FakeLLM:
            async def ainvoke(self, messages):
                class _R:
                    content = "您好，我是 CryptoMind！"

                return _R()

        return _FakeLLM()

    monkeypatch.setattr(ar, "resolve_user_llm_credentials", fake_resolve)
    monkeypatch.setattr(ar, "create_user_llm_client", fake_create)

    text = await ar._generate_greeting(
        display_name="測試使用者",
        wallet_address=None,
        language="zh-TW",
        tier="free",
        current_user={"user_id": "u1"},
    )

    assert "CryptoMind" in text
    # 確認用的是使用者的 BYOK，不是 server env key
    assert created["api_key"] == "sk-user-key"
    assert created["provider"] == "openai"


@pytest.mark.unit
async def test_generate_greeting_raises_when_no_byok(monkeypatch):
    """沒有 BYOK 時應 raise（由 endpoint fallback 到靜態招呼）。"""
    from api.routers import analysis as ar

    async def fake_resolve(current_user, preferred_provider=None):
        return None  # 沒有 BYOK

    monkeypatch.setattr(ar, "resolve_user_llm_credentials", fake_resolve)

    with pytest.raises(RuntimeError, match="no BYOK"):
        await ar._generate_greeting(
            display_name="x",
            wallet_address=None,
            language="zh-TW",
            tier="free",
            current_user={"user_id": "u1"},
        )


@pytest.mark.unit
async def test_generate_greeting_no_longer_reads_server_env(monkeypatch):
    """確認招呼語不再依賴 SERVER_OPENAI_API_KEY / OPENAI_API_KEY。"""

    from api.routers import analysis as ar

    # 把 server env 清掉，若有 BYOK 仍應正常（證明不依賴 server key）
    monkeypatch.delenv("SERVER_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def fake_resolve(current_user, preferred_provider=None):
        return {"provider": "openai", "api_key": "sk-byok", "model": "gpt-4o-mini"}

    def fake_create(*, provider, api_key, model, **kwargs):
        class _FakeLLM:
            async def ainvoke(self, messages):
                class _R:
                    content = "招呼"

                return _R()

        return _FakeLLM()

    monkeypatch.setattr(ar, "resolve_user_llm_credentials", fake_resolve)
    monkeypatch.setattr(ar, "create_user_llm_client", fake_create)

    # server env 都沒有，但有 BYOK → 應成功（不 raise）
    text = await ar._generate_greeting(
        display_name="x",
        wallet_address=None,
        language="zh-TW",
        tier="free",
        current_user={"user_id": "u1"},
    )
    assert text == "招呼"





@pytest.mark.unit
def test_sweep_greeting_cache_removes_expired_entries():
    """#6：_sweep_greeting_cache 應清掉所有過期 entry（session cache + user-recent）。"""
    import time

    # 用 router 的 dict（清乾淨避免污染）
    from api.routers import analysis as ar
    from api.routers.analysis import (
        _GREETING_CACHE_TTL_SECONDS,
        _GREETING_USER_RECENT_TTL_SECONDS,
        _sweep_greeting_cache,
    )

    saved = dict(ar._GREETING_CACHE)
    saved_u = dict(ar._GREETING_USER_RECENT)
    ar._GREETING_CACHE.clear()
    ar._GREETING_USER_RECENT.clear()
    try:
        now = time.time()
        # session cache：兩個過期 + 一個新鮮
        ar._GREETING_CACHE["u1:s1:zh"] = {"text": "old1", "ts": now - _GREETING_CACHE_TTL_SECONDS - 1, "fallback": False}
        ar._GREETING_CACHE["u2:s1:zh"] = {"text": "old2", "ts": now - _GREETING_CACHE_TTL_SECONDS - 100, "fallback": False}
        ar._GREETING_CACHE["u3:s1:zh"] = {"text": "fresh", "ts": now, "fallback": False}
        # user-recent：一個過期 + 一個新鮮
        ar._GREETING_USER_RECENT[("u1", "zh")] = {"text": "old-u", "ts": now - _GREETING_USER_RECENT_TTL_SECONDS - 1, "fallback": False}
        ar._GREETING_USER_RECENT[("u3", "zh")] = {"text": "fresh-u", "ts": now, "fallback": False}

        _sweep_greeting_cache(now)

        assert "u1:s1:zh" not in ar._GREETING_CACHE
        assert "u2:s1:zh" not in ar._GREETING_CACHE
        assert "u3:s1:zh" in ar._GREETING_CACHE  # 新鮮的留住
        assert ("u1", "zh") not in ar._GREETING_USER_RECENT
        assert ("u3", "zh") in ar._GREETING_USER_RECENT
    finally:
        ar._GREETING_CACHE.clear()
        ar._GREETING_CACHE.update(saved)
        ar._GREETING_USER_RECENT.clear()
        ar._GREETING_USER_RECENT.update(saved_u)


@pytest.mark.unit
def test_sweep_greeting_cache_empty_dict_is_noop():
    """#6：空 dict 呼叫 sweep 不出錯。"""
    from api.routers.analysis import _sweep_greeting_cache

    _sweep_greeting_cache(0.0)  # 不該 raise


@pytest.mark.unit
def test_invalidate_greeting_cache_clears_user_recent():
    """#7：invalidate_greeting_cache 應同時清 session cache 與 user-recent。
    改名後必須清 user-recent，否則舊暱稱招呼會透過 user-cache 持續回傳。
    """
    from api.routers import analysis as ar
    from api.routers.analysis import invalidate_greeting_cache

    saved = dict(ar._GREETING_CACHE)
    saved_u = dict(ar._GREETING_USER_RECENT)
    ar._GREETING_CACHE.clear()
    ar._GREETING_USER_RECENT.clear()
    try:
        ar._GREETING_CACHE["uX:s1:zh"] = {"text": "x", "ts": 0, "fallback": False}
        ar._GREETING_CACHE["other:s1:zh"] = {"text": "y", "ts": 0, "fallback": False}
        ar._GREETING_USER_RECENT[("uX", "zh")] = {"text": "x", "ts": 0, "fallback": False}
        ar._GREETING_USER_RECENT[("other", "zh")] = {"text": "y", "ts": 0, "fallback": False}

        cleared = invalidate_greeting_cache("uX")

        # uX 的兩個 entry 都清掉（session + user-recent = 2）；other 保留
        assert cleared == 2
        assert "uX:s1:zh" not in ar._GREETING_CACHE
        assert "other:s1:zh" in ar._GREETING_CACHE
        assert ("uX", "zh") not in ar._GREETING_USER_RECENT
        assert ("other", "zh") in ar._GREETING_USER_RECENT
    finally:
        ar._GREETING_CACHE.clear()
        ar._GREETING_CACHE.update(saved)
        ar._GREETING_USER_RECENT.clear()
        ar._GREETING_USER_RECENT.update(saved_u)
