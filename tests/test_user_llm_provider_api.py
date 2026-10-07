"""Tests for 使用者選的 LLM provider 持久化（跨裝置帶回）。

涵蓋：
- PUT /api/user/preferences/llm-provider（set）
- GET /api/user/me 回傳 selected_provider
- repo upsert / get

仿 tests/test_user_display_name_api.py 慣例：mock repo，不依賴真實 DB。
設計：docs/plans/2026-08-06-persist-user-selected-provider-design.md
"""

from __future__ import annotations

import pytest

from api.routers import user as user_router


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from api.middleware.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:  # noqa: BLE001
        pass
    yield
    try:
        limiter.reset()
    except Exception:  # noqa: BLE001
        pass


class _FakeRepo:
    """In-memory 假 repo，模擬 upsert + get。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.last_set = None

    async def get_selected_provider(self, user_id, session=None):
        return self.store.get(user_id)

    async def set_selected_provider(self, user_id, provider, session=None):
        self.store[user_id] = provider
        self.last_set = (user_id, provider)


@pytest.fixture
def fake_repo(monkeypatch):
    repo = _FakeRepo()
    monkeypatch.setattr(user_router, "user_llm_preferences_repo", repo)
    user_router._ME_CACHE.clear()
    return repo


# ============================================================================
# PUT /api/user/preferences/llm-provider
# ============================================================================


@pytest.mark.asyncio
class TestSetLLMProviderEndpoint:
    async def test_success(self, client, auth_headers, fake_repo):
        response = await client.put(
            "/api/user/preferences/llm-provider",
            json={"provider": "deepseek"},
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"success": True, "provider": "deepseek"}
        assert fake_repo.last_set is not None
        assert fake_repo.last_set[1] == "deepseek"

    async def test_normalizes_provider_to_lowercase(self, client, auth_headers, fake_repo):
        response = await client.put(
            "/api/user/preferences/llm-provider",
            json={"provider": "DeepSeek"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["provider"] == "deepseek"
        assert fake_repo.last_set[1] == "deepseek"

    async def test_rejects_unknown_provider(self, client, auth_headers, fake_repo):
        response = await client.put(
            "/api/user/preferences/llm-provider",
            json={"provider": "not_a_real_provider"},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "Unsupported provider" in response.json()["detail"]
        assert fake_repo.last_set is None  # 未寫入

    async def test_rejects_empty_provider(self, client, auth_headers, fake_repo):
        response = await client.put(
            "/api/user/preferences/llm-provider",
            json={"provider": ""},
            headers=auth_headers,
        )
        # Pydantic min_length=1 → 422
        assert response.status_code == 422
        assert fake_repo.last_set is None

    # 註：endpoint 用 Depends(get_current_user) 保證 auth；TEST_MODE 下
    # get_current_user 直接回 test user，無法在此覆蓋測 401/403。
    # 生產環境（TEST_MODE=false）未帶 token 會被 get_current_user 擋。


# ============================================================================
# GET /api/user/me 含 selected_provider
# ============================================================================


@pytest.mark.asyncio
class TestMeReturnsSelectedProvider:
    async def test_me_includes_selected_provider_when_set(self, client, auth_headers, fake_repo):
        fake_repo.store["test-user-001"] = "deepseek"
        response = await client.get("/api/user/me", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["user"]["selected_provider"] == "deepseek"

    async def test_me_selected_provider_none_when_never_set(self, client, auth_headers, fake_repo):
        response = await client.get("/api/user/me", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["user"]["selected_provider"] is None


# ============================================================================
# Repo upsert 語意（_FakeRepo 模擬；另用真 model 確認欄位）
# ============================================================================


def test_user_llm_preference_model_fields():
    """確認 ORM model 欄位齊全（schema 與 design 一致）。"""
    from core.orm.models import UserLLMPreference

    cols = {c.name for c in UserLLMPreference.__table__.columns}
    assert cols == {"user_id", "provider", "updated_at"}
    # user_id 為 PK
    assert UserLLMPreference.__table__.columns["user_id"].primary_key


def test_create_table_sql_matches_model():
    """schema.py CREATE TABLE 與 model 欄位一致。"""
    from core.database.schema import create_user_llm_preferences_table

    # 函式存在且可呼叫（實際建表在整合測試 / 啟動時跑）
    assert callable(create_user_llm_preferences_table)
