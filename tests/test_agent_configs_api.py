"""Agent Configs API 測試 — api/routers/agent_configs.py（Model Mixer Step 1）。

設計：docs/plans/2026-09-02-model-mixer-step1-schema.md（DANNY 拍板方案 B）。
治理邊界與 agent_presets API 同套：flag off → 404、寫入 Premium-only、
server 端驗證 agent_id／provider／model、變更後失效 manager cache。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolate_rate_limits():
    from api.middleware.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


def _make_app(user: dict, presets_enabled: bool = True):
    from fastapi import FastAPI

    from api.routers import agent_configs as ac_module

    app = FastAPI()
    app.include_router(ac_module.router)

    async def fake_user():
        return user

    async def fake_session():
        yield None  # repo 被 mock，session 不會被用到

    app.dependency_overrides[ac_module.get_current_user] = fake_user
    app.dependency_overrides[ac_module.get_async_session] = fake_session

    enabled = presets_enabled

    async def fake_owned_preset(_session, _current_user, preset_id):
        # c044：endpoint 一律先驗 preset 屬於本人。本檔測的是 config 語義，
        # 擁有權另有專門測試（TestPresetOwnership）。
        return SimpleNamespace(preset_id=preset_id, user_id=user["user_id"])

    with patch("api.routers.agent_configs.agent_presets_enabled", return_value=enabled), \
            patch("api.routers.agent_configs._owned_preset", fake_owned_preset):
        yield app


PRESET_ID = "p_test"


FREE_USER = {"user_id": "u_free", "username": "free", "membership_tier": "free"}
PREMIUM_USER = {"user_id": "u_prem", "username": "prem", "membership_tier": "premium"}


def _fake_config(**overrides):
    data = {
        "agent_id": "general_research",
        "model_selection": {"provider": "openai", "model": "gpt-5.6-luna"},
        "tools": [],
        "skills": [],
        # 2026-09-09 起 _config_to_dict 會讀 system_prompt（per-agent prompt）
        "system_prompt": None,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TestFeatureFlag:
    def test_disabled_returns_404(self):
        gen = _make_app(PREMIUM_USER, presets_enabled=False)
        app = next(gen)
        try:
            client = TestClient(app)
            assert client.get(f"/api/agent-presets/{PRESET_ID}/agent-configs").status_code == 404
            assert (
                client.put(
                    f"/api/agent-presets/{PRESET_ID}/agent-configs/general_research",
                    json={"model_selection": None},
                ).status_code
                == 404
            )
        finally:
            next(gen, None)


class TestListConfigs:
    def test_free_user_can_read(self):
        gen = _make_app(FREE_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_configs.agent_configs_repo.list_configs",
                new=AsyncMock(return_value=[]),
            ):
                resp = client.get(f"/api/agent-presets/{PRESET_ID}/agent-configs")
            assert resp.status_code == 200
            assert resp.json() == {"success": True, "configs": []}
        finally:
            next(gen, None)

    def test_lists_existing_configs(self):
        gen = _make_app(FREE_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_configs.agent_configs_repo.list_configs",
                new=AsyncMock(return_value=[_fake_config()]),
            ):
                resp = client.get(f"/api/agent-presets/{PRESET_ID}/agent-configs")
            data = resp.json()
            assert data["configs"][0]["agent_id"] == "general_research"
            assert data["configs"][0]["model_selection"]["model"] == "gpt-5.6-luna"
        finally:
            next(gen, None)


class TestSetConfig:
    def _put(self, client, agent_id="general_research", **payload):
        return client.put(f"/api/agent-presets/{PRESET_ID}/agent-configs/{agent_id}", json=payload)

    def test_free_user_forbidden(self):
        gen = _make_app(FREE_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            resp = self._put(
                client,
                model_selection={"provider": "openai", "model": "gpt-5.6-luna"},
            )
            assert resp.status_code == 403
        finally:
            next(gen, None)

    def test_unknown_agent_id_rejected(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            resp = self._put(
                client,
                agent_id="no_such_agent",
                model_selection={"provider": "openai", "model": "gpt-5.6-luna"},
            )
            assert resp.status_code == 400
            assert "no_such_agent" in resp.json()["detail"]
        finally:
            next(gen, None)

    def test_unsupported_provider_rejected(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            resp = self._put(
                client,
                model_selection={"provider": "no_such", "model": "gpt-5.6-luna"},
            )
            assert resp.status_code == 400
            assert "provider" in resp.json()["detail"]
        finally:
            next(gen, None)

    def test_fixed_list_provider_rejects_unknown_model(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            resp = self._put(
                client,
                model_selection={"provider": "openai", "model": "totally-fake"},
            )
            assert resp.status_code == 400
            assert "Unknown model" in resp.json()["detail"]
        finally:
            next(gen, None)

    def test_free_input_provider_accepts_arbitrary_model(self):
        """openrouter 等自由輸入 provider：任意非空字串都放行（前端是文字框）。"""
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_configs.agent_configs_repo.set_config",
                new=AsyncMock(
                    return_value=_fake_config(
                        model_selection={
                            "provider": "openrouter",
                            "model": "some/vendor-model",
                        }
                    )
                ),
            ), patch("api.routers.agent_configs.agent_configs_repo.get_config",
                     new=AsyncMock(return_value=None)), patch(
                "api.routers.agent_configs.invalidate_manager_cache"
            ):
                resp = self._put(
                    client,
                    model_selection={
                        "provider": "openrouter",
                        "model": "some/vendor-model",
                    },
                )
            assert resp.status_code == 200
        finally:
            next(gen, None)

    def test_premium_set_success_invalidates_cache(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_configs.agent_configs_repo.set_config",
                new=AsyncMock(return_value=_fake_config()),
            ) as mock_set, patch(
                "api.routers.agent_configs.agent_configs_repo.get_config",
                new=AsyncMock(return_value=None),
            ), patch(
                "api.routers.agent_configs.invalidate_manager_cache"
            ) as mock_inv:
                resp = self._put(
                    client,
                    model_selection={"provider": "openai", "model": "gpt-5.6-luna"},
                )
            assert resp.status_code == 200
            assert resp.json()["config"]["model_selection"]["provider"] == "openai"
            mock_set.assert_awaited_once_with(
                None, PRESET_ID, "general_research", "u_prem",
                model_selection={"provider": "openai", "model": "gpt-5.6-luna"},
            )
            mock_inv.assert_called_once_with("u_prem")
        finally:
            next(gen, None)

    def test_null_model_selection_resets_to_default(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_configs.agent_configs_repo.set_config",
                new=AsyncMock(
                    return_value=_fake_config(model_selection=None)
                ),
            ), patch("api.routers.agent_configs.agent_configs_repo.get_config",
                     new=AsyncMock(return_value=None)), patch(
                "api.routers.agent_configs.invalidate_manager_cache"
            ):
                resp = self._put(client, model_selection=None)
            assert resp.status_code == 200
            assert resp.json()["config"]["model_selection"] is None
        finally:
            next(gen, None)


class TestToolsConfig:
    """Mixer Step 2：PUT 的 tools 欄（absent=不更動、null=重設、list=白名單）。

    對應 design：docs/plans/2026-09-03-model-mixer-step2-tools.md §4。
    """

    def _put(self, client, agent_id="general_research", **payload):
        return client.put(f"/api/agent-presets/{PRESET_ID}/agent-configs/{agent_id}", json=payload)

    def _patches(self, existing=None, saved=None):
        return (
            patch(
                "api.routers.agent_configs.agent_configs_repo.set_config",
                new=AsyncMock(return_value=saved or _fake_config()),
            ),
            patch(
                "api.routers.agent_configs.agent_configs_repo.get_config",
                new=AsyncMock(return_value=existing),
            ),
            patch("api.routers.agent_configs.invalidate_manager_cache"),
        )

    def test_put_tools_saves_normalized_whitelist(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            saved = _fake_config(tools=["fetch_url", "web_search"])
            p_set, p_get, p_inv = self._patches(saved=saved)
            with p_set as mock_set, p_get, p_inv:
                resp = self._put(
                    client,
                    tools=["web_search", " fetch_url ", "web_search"],
                )
            assert resp.status_code == 200
            # 去重、去空白、排序後儲存
            mock_set.assert_awaited_once_with(
                None, PRESET_ID, "general_research", "u_prem",
                tools=["fetch_url", "web_search"],
            )
            assert resp.json()["config"]["tools"] == ["fetch_url", "web_search"]
        finally:
            next(gen, None)

    def test_put_unknown_tool_name_rejected(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            p_set, p_get, p_inv = self._patches()
            with p_set as mock_set, p_get, p_inv:
                resp = self._put(client, tools=["web_search", "not_a_tool"])
            assert resp.status_code == 400
            assert "not_a_tool" in resp.json()["detail"]
            mock_set.assert_not_awaited()
        finally:
            next(gen, None)

    @pytest.mark.parametrize("tools", [[""], ["   "], ["x" * 101], ["web_search", "x" * 101]])
    def test_invalid_tool_names_never_reset_or_truncate_selection(self, tools):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            p_set, p_get, p_inv = self._patches()
            with p_set as write, p_get, p_inv as invalidate:
                resp = self._put(TestClient(app), tools=tools)
            assert resp.status_code == 400
            write.assert_not_awaited()
            invalidate.assert_not_called()
            assert len(resp.json()["detail"]) < 100
        finally:
            next(gen, None)

    def test_absent_tools_preserves_existing(self):
        """tools 欄缺席 = 不更動（混合版本／model-only PUT 安全）。"""
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            existing = _fake_config(tools=["web_search"])
            p_set, p_get, p_inv = self._patches(existing=existing)
            with p_set as mock_set, p_get as mock_get, p_inv:
                resp = self._put(
                    client,
                    model_selection={"provider": "openai", "model": "gpt-5.6-luna"},
                )
            assert resp.status_code == 200
            mock_get.assert_not_awaited()
            mock_set.assert_awaited_once_with(
                None, PRESET_ID, "general_research", "u_prem",
                model_selection={"provider": "openai", "model": "gpt-5.6-luna"},
            )
        finally:
            next(gen, None)

    def test_absent_tools_without_existing_row_defaults_empty(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            p_set, p_get, p_inv = self._patches(existing=None)
            with p_set as mock_set, p_get, p_inv:
                resp = self._put(
                    client,
                    model_selection={"provider": "openai", "model": "gpt-5.6-luna"},
                )
            assert resp.status_code == 200
            assert "tools" not in mock_set.await_args.kwargs
        finally:
            next(gen, None)

    def test_null_tools_resets_to_unconfigured(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            saved = _fake_config(tools=[], model_selection=None)
            p_set, p_get, p_inv = self._patches(saved=saved)
            with p_set as mock_set, p_get, p_inv:
                resp = self._put(
                    client, model_selection=None, tools=None,
                )
            assert resp.status_code == 200
            mock_set.assert_awaited_once_with(
                None, PRESET_ID, "general_research", "u_prem", model_selection=None, tools=[],
            )
        finally:
            next(gen, None)

    def test_tools_only_put_preserves_model_selection(self):
        """tools-only PUT 不得清掉已存的 model_selection（對稱的 absent 語義）。"""
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            existing = _fake_config(
                model_selection={"provider": "openai", "model": "gpt-5.6-luna"},
                tools=["fetch_url"],
            )
            p_set, p_get, p_inv = self._patches(existing=existing)
            with p_set as mock_set, p_get, p_inv:
                resp = self._put(client, tools=["web_search"])
            assert resp.status_code == 200
            mock_set.assert_awaited_once_with(
                None, PRESET_ID, "general_research", "u_prem",
                tools=["web_search"],
            )
        finally:
            next(gen, None)

    def test_mcp_capability_tool_names_accepted(self):
        """MCP 工具（CAPABILITY_TOOL_MAP）也是合法白名單成員。"""
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            p_set, p_get, p_inv = self._patches()
            with p_set as mock_set, p_get, p_inv:
                resp = self._put(
                    client, agent_id="people_projects", tools=["search_projects"],
                )
            assert resp.status_code == 200
            assert mock_set.await_args.kwargs["tools"] == ["search_projects"]
        finally:
            next(gen, None)

    def test_tools_over_limit_rejected(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            p_set, p_get, p_inv = self._patches()
            with p_set, p_get, p_inv:
                resp = self._put(client, tools=[f"t{i:03d}" for i in range(101)])
            assert resp.status_code in (400, 422)
        finally:
            next(gen, None)

    def test_both_fields_absent_rejected(self):
        """PUT {}（兩欄皆 absent）→ 400，不得誤寫成整列重設。"""
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            p_set, p_get, p_inv = self._patches()
            with p_set as mock_set, p_get, p_inv:
                resp = self._put(client)
            assert resp.status_code == 400
            mock_set.assert_not_awaited()
        finally:
            next(gen, None)

    def test_free_user_cannot_set_tools(self):
        gen = _make_app(FREE_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            p_set, p_get, p_inv = self._patches()
            with p_set as mock_set, p_get, p_inv:
                resp = self._put(client, tools=["web_search"])
            assert resp.status_code == 403
            mock_set.assert_not_awaited()
        finally:
            next(gen, None)

    def test_list_returns_tools(self):
        gen = _make_app(FREE_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_configs.agent_configs_repo.list_configs",
                new=AsyncMock(return_value=[_fake_config(tools=["web_search"])]),
            ):
                resp = client.get(f"/api/agent-presets/{PRESET_ID}/agent-configs")
            assert resp.status_code == 200
            assert resp.json()["configs"][0]["tools"] == ["web_search"]
        finally:
            next(gen, None)


class TestDeleteConfig:
    def test_free_user_forbidden(self):
        gen = _make_app(FREE_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            resp = client.delete(f"/api/agent-presets/{PRESET_ID}/agent-configs/general_research")
            assert resp.status_code == 403
        finally:
            next(gen, None)

    def test_missing_config_404(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_configs.agent_configs_repo.delete_config",
                new=AsyncMock(return_value=False),
            ):
                resp = client.delete(f"/api/agent-presets/{PRESET_ID}/agent-configs/general_research")
            assert resp.status_code == 404
        finally:
            next(gen, None)

    def test_delete_success(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_configs.agent_configs_repo.delete_config",
                new=AsyncMock(return_value=True),
            ), patch(
                "api.routers.agent_configs.invalidate_manager_cache"
            ) as mock_inv:
                resp = client.delete(f"/api/agent-presets/{PRESET_ID}/agent-configs/general_research")
            assert resp.status_code == 200
            mock_inv.assert_called_once_with("u_prem")
        finally:
            next(gen, None)


class TestPresetScopeIsTheOnlyScope:
    """c044：per-agent 設定只存在於 preset——沒有使用者層、沒有 fallback。

    在此之前主鍵是 (user_id, agent_id)，所有 preset 共用同一份設定：改一個
    preset 的工具，另一個跟著變，而 UI 上完全看不出來。治標的做法是加一層
    fallback（preset 沒設就用使用者層），那會做出「兩個地方能設同一件事、
    哪個贏看不見」的結構——正是這個專案反覆出事的那一類。
    """

    def test_route_carries_preset_id_so_it_cannot_be_forgotten(self):
        """作用域在路徑上。放成選填 query param 的話，漏帶就會操作到別的範圍。"""
        from api.routers import agent_configs as ac

        paths = {r.path for r in ac.router.routes}
        assert "/api/agent-presets/{preset_id}/agent-configs" in paths
        assert "/api/agent-presets/{preset_id}/agent-configs/{agent_id}" in paths
        # 舊的使用者層路徑必須消失，不能兩套並存
        assert "/api/agent-configs" not in paths
        assert "/api/agent-configs/{agent_id}" not in paths

    def test_repo_signature_is_preset_scoped(self):
        """repo 不得再收 user_id 當作用域鍵。"""
        import inspect

        from core.orm.agent_configs_repo import AgentConfigsRepository

        for name in ("list_configs", "get_config", "delete_config"):
            params = list(inspect.signature(getattr(AgentConfigsRepository, name)).parameters)
            assert "preset_id" in params, f"{name} 少了 preset_id"
            assert "user_id" not in params, f"{name} 仍以 user_id 當作用域"

    def test_tool_loader_requires_preset(self):
        """執行期載入器沒有 preset 就不套用，不得退回使用者層全域設定。"""
        import inspect

        from api.routers.agent_configs import load_agent_tool_selections

        assert "preset_id" in inspect.signature(load_agent_tool_selections).parameters

    def test_orm_primary_key_is_preset_and_agent(self):
        from core.orm.models import UserAgentConfig

        pk = {c.name for c in UserAgentConfig.__table__.primary_key.columns}
        assert pk == {"preset_id", "agent_id"}

    def test_config_row_dies_with_its_preset(self):
        """ON DELETE CASCADE：刪 preset 時它的設定跟著消失，不留孤兒列。"""
        from core.orm.models import UserAgentConfig

        fks = [
            fk for c in UserAgentConfig.__table__.columns for fk in c.foreign_keys
            if c.name == "preset_id"
        ]
        assert fks, "preset_id 沒有外鍵"
        assert fks[0].ondelete == "CASCADE"


class TestPresetOwnership:
    """endpoint 必須驗 preset 屬於本人——否則帶別人的 preset_id 就能讀寫其設定。"""

    def _app_without_ownership_patch(self, user):
        from unittest.mock import patch

        from fastapi import FastAPI

        from api.routers import agent_configs as ac_module

        app = FastAPI()
        app.include_router(ac_module.router)

        async def fake_user():
            return user

        async def fake_session():
            yield None

        app.dependency_overrides[ac_module.get_current_user] = fake_user
        app.dependency_overrides[ac_module.get_async_session] = fake_session
        return app, patch

    def test_other_users_preset_is_404(self):
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        app, _ = self._app_without_ownership_patch(PREMIUM_USER)
        with patch("api.routers.agent_configs.agent_presets_enabled", return_value=True), \
                patch("core.orm.agent_presets_repo.agent_presets_repo.get_preset",
                      new=AsyncMock(return_value=None)):
            client = TestClient(app)
            assert client.get("/api/agent-presets/someone_elses/agent-configs").status_code == 404
            assert client.delete(
                "/api/agent-presets/someone_elses/agent-configs/general_research"
            ).status_code == 404


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class TestSkillsConfig:
    """Mixer Step 4：PUT 的 skills 欄（absent=不更動、null/[]=重設、list=白名單）。

    對應 design：docs/plans/2026-09-08-mixer-step4-per-agent-skills-design.md。
    語意與 TestToolsConfig 完全對稱——tools 有的治理邊界 skills 都要有。
    """

    _KNOWN = {"crypto-technical-analysis", "us-stock-technical", "investment-judgment"}

    def _put(self, client, agent_id="general_research", **payload):
        return client.put(f"/api/agent-presets/{PRESET_ID}/agent-configs/{agent_id}", json=payload)

    def _patches(self, existing=None, saved=None):
        return (
            patch(
                "api.routers.agent_configs.agent_configs_repo.set_config",
                new=AsyncMock(return_value=saved or _fake_config()),
            ),
            patch(
                "api.routers.agent_configs.agent_configs_repo.get_config",
                new=AsyncMock(return_value=existing),
            ),
            patch("api.routers.agent_configs.invalidate_manager_cache"),
            patch(
                "api.routers.agent_configs._known_skill_names",
                return_value=set(self._KNOWN),
            ),
        )

    def test_put_skills_saves_normalized_whitelist(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            saved = _fake_config(skills=["crypto-technical-analysis", "investment-judgment"])
            p_set, p_get, p_inv, p_names = self._patches(saved=saved)
            with p_set as mock_set, p_get, p_inv, p_names:
                resp = self._put(
                    client,
                    skills=["investment-judgment", " crypto-technical-analysis ", "investment-judgment"],
                )
            assert resp.status_code == 200
            mock_set.assert_awaited_once_with(
                None, PRESET_ID, "general_research", "u_prem",
                skills=["crypto-technical-analysis", "investment-judgment"],
            )
            assert resp.json()["config"]["skills"] == ["crypto-technical-analysis", "investment-judgment"]
        finally:
            next(gen, None)

    def test_put_unknown_skill_name_rejected(self):
        """未知名稱整筆拒絕——絕不靜默把錯誤輸入降成 []（官方預設）。"""
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            p_set, p_get, p_inv, p_names = self._patches()
            with p_set as mock_set, p_get, p_inv, p_names:
                resp = self._put(client, skills=["investment-judgment", "not_a_skill"])
            assert resp.status_code == 400
            assert "not_a_skill" in resp.json()["detail"]
            mock_set.assert_not_awaited()
        finally:
            next(gen, None)

    def test_invalid_skill_names_never_reset_selection(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            p_set, p_get, p_inv, p_names = self._patches()
            with p_set as write, p_get, p_inv as invalidate, p_names:
                resp = self._put(TestClient(app), skills=["x" * 101])
            assert resp.status_code == 400
            write.assert_not_awaited()
            invalidate.assert_not_called()
            assert len(resp.json()["detail"]) < 100
        finally:
            next(gen, None)

    def test_absent_skills_preserves_existing(self):
        """skills 欄缺席 = 不更動（model-only / tools-only PUT 安全）。"""
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            p_set, p_get, p_inv, p_names = self._patches()
            with p_set as mock_set, p_get, p_inv, p_names:
                resp = self._put(
                    client,
                    model_selection={"provider": "openai", "model": "gpt-5.6-luna"},
                )
            assert resp.status_code == 200
            assert "skills" not in mock_set.await_args.kwargs
        finally:
            next(gen, None)

    def test_null_skills_resets_to_unconfigured(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            saved = _fake_config(skills=[])
            p_set, p_get, p_inv, p_names = self._patches(saved=saved)
            with p_set as mock_set, p_get, p_inv, p_names:
                resp = self._put(client, skills=None)
            assert resp.status_code == 200
            mock_set.assert_awaited_once_with(
                None, PRESET_ID, "general_research", "u_prem", skills=[],
            )
        finally:
            next(gen, None)

    def test_get_lists_skills_field(self):
        gen = _make_app(FREE_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_configs.agent_configs_repo.list_configs",
                new=AsyncMock(return_value=[_fake_config(skills=["investment-judgment"])]),
            ):
                resp = client.get(f"/api/agent-presets/{PRESET_ID}/agent-configs")
            assert resp.status_code == 200
            assert resp.json()["configs"][0]["skills"] == ["investment-judgment"]
        finally:
            next(gen, None)


class TestLoadSkillSelections:
    """執行期載入器：Premium 重驗、非空白名單才收、失敗降級 None（比照 tools）。"""

    def test_premium_collects_non_empty_selections(self):
        from api.routers.agent_configs import load_agent_skill_selections

        configs = [
            _fake_config(agent_id="general_research", skills=["investment-judgment"]),
            _fake_config(agent_id="finance_markets", skills=[]),  # 空＝未設定，不收
        ]
        with patch(
            "api.routers.agent_configs.agent_configs_repo.list_configs",
            new=AsyncMock(return_value=configs),
        ):
            result = _run(load_agent_skill_selections(None, PREMIUM_USER, PRESET_ID))
        assert result == {"general_research": ["investment-judgment"]}

    def test_free_user_gets_none_even_with_stored_selections(self):
        """降級後殘留的勾選不得繼續生效——讀取端重驗 tier。"""
        from api.routers.agent_configs import load_agent_skill_selections

        configs = [_fake_config(agent_id="general_research", skills=["investment-judgment"])]
        with patch(
            "api.routers.agent_configs.agent_configs_repo.list_configs",
            new=AsyncMock(return_value=configs),
        ):
            assert _run(load_agent_skill_selections(None, FREE_USER, PRESET_ID)) is None

    def test_all_empty_returns_none(self):
        from api.routers.agent_configs import load_agent_skill_selections

        with patch(
            "api.routers.agent_configs.agent_configs_repo.list_configs",
            new=AsyncMock(return_value=[_fake_config(skills=[])]),
        ):
            assert _run(load_agent_skill_selections(None, PREMIUM_USER, PRESET_ID)) is None

    def test_repo_failure_degrades_to_none(self):
        from api.routers.agent_configs import load_agent_skill_selections

        with patch(
            "api.routers.agent_configs.agent_configs_repo.list_configs",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            assert _run(load_agent_skill_selections(None, PREMIUM_USER, PRESET_ID)) is None
