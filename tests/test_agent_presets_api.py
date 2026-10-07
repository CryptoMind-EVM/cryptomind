"""Agent Presets API 測試 — api/routers/agent_presets.py（impl plan Task B4）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _make_app(user: dict, presets_enabled: bool = True):
    from fastapi import FastAPI

    from api.routers import agent_presets as ap_module

    app = FastAPI()
    app.include_router(ap_module.router)

    async def fake_user():
        return user

    async def fake_session():
        yield None  # repo 被 mock，session 不會被用到

    # suite 內其他測試模組在 module-level 執行 importlib.reload（如
    # tests/e2e/test_settings.py:81、tests/security/test_security_hardening.py:215），
    # collection 期綁定的 api.deps 符號可能已與 router 內的不同。
    # 改用 router 模組「當下」的符號作為 override key，確保與 endpoint
    # 實際 Depends 一致（免疫既有測試基建的順序相依污染）。
    app.dependency_overrides[ap_module.get_current_user] = fake_user
    app.dependency_overrides[ap_module.get_async_session] = fake_session

    enabled = presets_enabled
    with patch("api.routers.agent_presets.agent_presets_enabled", return_value=enabled):
        yield app


FREE_USER = {"user_id": "u_free", "username": "free", "membership_tier": "free"}
PREMIUM_USER = {"user_id": "u_prem", "username": "prem", "membership_tier": "premium"}


def _fake_preset(**overrides):
    data = {
        "preset_id": "prst_abc",
        "user_id": "u_prem",
        "name": "My Research Team",
        "mode": "single",
        "agent_ids": ["people_projects"],
        "analysis_mode": "quick",
        "action_policy": "read_only",
        "capability_overrides": {},
        "is_default": False,
        "config_version": "2026.08.0",
        "created_at": None,
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
            resp = client.get("/api/agent-profiles")
            assert resp.status_code == 404
        finally:
            next(gen, None)


class TestProfiles:
    def test_lists_official_profiles(self):
        gen = _make_app(FREE_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            resp = client.get("/api/agent-profiles")
            assert resp.status_code == 200
            data = resp.json()
            ids = {p["id"] for p in data["profiles"]}
            assert {
                "general_research",
                "finance_markets",
                "people_projects",
                "onchain_security",
            } <= ids
            assert data["config_version"]
        finally:
            next(gen, None)


class TestListPresets:
    def test_free_user_gets_official_default(self):
        gen = _make_app(FREE_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_presets.agent_presets_repo.list_presets",
                new=AsyncMock(return_value=[]),
            ):
                resp = client.get("/api/agent-presets")
            assert resp.status_code == 200
            data = resp.json()
            assert data["presets"] == []
            assert data["quota"] == {"max": 10, "used": 0}
            assert data["official_default"]["agent_ids"] == ["general_research"]
        finally:
            next(gen, None)


class TestCreatePreset:
    def _post(self, client, payload):
        return client.post("/api/agent-presets", json=payload)

    def test_free_user_forbidden(self):
        gen = _make_app(FREE_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            resp = self._post(client, {"name": "X", "agent_ids": ["general_research"]})
            assert resp.status_code == 403
        finally:
            next(gen, None)

    def test_unknown_agent_id_rejected(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            resp = self._post(client, {"name": "X", "agent_ids": ["no_such"]})
            assert resp.status_code == 400
            assert "no_such" in resp.json()["detail"]
        finally:
            next(gen, None)

    def test_unknown_capability_rejected(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            resp = self._post(
                client,
                {
                    "name": "X",
                    "agent_ids": ["general_research"],
                    "capability_overrides": {"quantum": True},
                },
            )
            assert resp.status_code == 400
        finally:
            next(gen, None)

    def test_more_than_four_agents_rejected(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            resp = self._post(
                client,
                {
                    "name": "X",
                    "agent_ids": [
                        "general_research",
                        "finance_markets",
                        "people_projects",
                        "onchain_security",
                        "general_research",
                    ],
                },
            )
            assert resp.status_code == 422  # Pydantic max_length
        finally:
            next(gen, None)

    def test_premium_create_success(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with (
                patch(
                    "api.routers.agent_presets.agent_presets_repo.count_presets",
                    new=AsyncMock(return_value=0),
                ),
                patch(
                    "api.routers.agent_presets.agent_presets_repo.get_default_preset",
                    new=AsyncMock(return_value=_fake_preset()),
                ),
                patch(
                    "api.routers.agent_presets.agent_presets_repo.create_preset",
                    new=AsyncMock(return_value=_fake_preset()),
                ),
                patch("api.routers.agent_presets.invalidate_manager_cache") as mock_inv,
            ):
                resp = self._post(
                    client,
                    {"name": "My Research Team", "agent_ids": ["people_projects"]},
                )
            assert resp.status_code == 201
            assert resp.json()["preset"]["preset_id"] == "prst_abc"
            mock_inv.assert_called_once_with("u_prem")
        finally:
            next(gen, None)

    def test_first_preset_becomes_active_automatically(self):
        """2026-09-22 DANNY：沒有作用中 preset 時新建的直接設為作用中（否則 Agents 分頁模型下拉全停用）。"""
        for existing_default, expect in ((None, True), (_fake_preset(), False)):
            gen = _make_app(PREMIUM_USER)
            app = next(gen)
            try:
                client = TestClient(app)
                with (
                    patch(
                        "api.routers.agent_presets.agent_presets_repo.count_presets",
                        new=AsyncMock(return_value=0),
                    ),
                    patch(
                        "api.routers.agent_presets.agent_presets_repo.get_default_preset",
                        new=AsyncMock(return_value=existing_default),
                    ),
                    patch(
                        "api.routers.agent_presets.agent_presets_repo.create_preset",
                        new=AsyncMock(return_value=_fake_preset()),
                    ) as mock_create,
                    patch("api.routers.agent_presets.invalidate_manager_cache"),
                ):
                    resp = self._post(
                        client, {"name": "Team", "agent_ids": ["people_projects"]}
                    )
                    assert resp.status_code == 201
                    assert mock_create.await_args.kwargs["is_default"] is expect
                    resp = client.post(
                        "/api/agent-presets/from-template/token_diligence"
                    )
                    assert resp.status_code == 201, resp.text
                    assert mock_create.await_args.kwargs["is_default"] is expect
            finally:
                next(gen, None)

    def test_preset_limit_409(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_presets.agent_presets_repo.count_presets",
                new=AsyncMock(return_value=10),
            ):
                resp = self._post(
                    client, {"name": "X", "agent_ids": ["general_research"]}
                )
            assert resp.status_code == 409
            assert "limit" in resp.json()["detail"].lower()
        finally:
            next(gen, None)


class TestMutations:
    def test_patch_nonexistent_404(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_presets.agent_presets_repo.get_preset",
                new=AsyncMock(return_value=None),
            ):
                resp = client.patch(
                    "/api/agent-presets/prst_ghost", json={"name": "New"}
                )
            assert resp.status_code == 404
        finally:
            next(gen, None)

    def test_delete_nonexistent_404(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_presets.agent_presets_repo.delete_preset",
                new=AsyncMock(return_value=False),
            ):
                resp = client.delete("/api/agent-presets/prst_ghost")
            assert resp.status_code == 404
        finally:
            next(gen, None)

    def test_activate_success_with_summary(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with (
                patch(
                    "api.routers.agent_presets.agent_presets_repo.set_default",
                    new=AsyncMock(return_value=True),
                ),
                patch(
                    "api.routers.agent_presets.agent_presets_repo.get_preset",
                    new=AsyncMock(return_value=_fake_preset()),
                ),
                patch("api.routers.agent_presets.invalidate_manager_cache"),
            ):
                resp = client.post("/api/agent-presets/prst_abc/activate")
            assert resp.status_code == 200
            summary = resp.json()["summary"]
            assert summary["tool_count"] > 0
            assert "people_projects" in summary["profiles"]
            assert summary["config_hash"]
        finally:
            next(gen, None)

    def test_resolved_capabilities(self):
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_presets.agent_presets_repo.get_preset",
                new=AsyncMock(return_value=_fake_preset()),
            ):
                resp = client.get("/api/agent-presets/prst_abc/resolved-capabilities")
            assert resp.status_code == 200
            resolved = resp.json()["resolved"]
            assert "search_projects" in resolved["tool_names"]
            assert "get_user_balances" not in resolved["tool_names"]
        finally:
            next(gen, None)

    def test_resolved_capabilities_other_users_preset_404(self):
        """他人 preset 查不到（防枚舉）。"""
        gen = _make_app(PREMIUM_USER)
        app = next(gen)
        try:
            client = TestClient(app)
            with patch(
                "api.routers.agent_presets.agent_presets_repo.get_preset",
                new=AsyncMock(return_value=None),
            ):
                resp = client.get("/api/agent-presets/prst_other/resolved-capabilities")
            assert resp.status_code == 404
        finally:
            next(gen, None)
