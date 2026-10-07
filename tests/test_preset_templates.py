"""團隊模板：列表四語、一鍵建立走與 create 同一條限制。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

PREMIUM = {"user_id": "u_prem", "username": "prem", "membership_tier": "premium"}
FREE = {"user_id": "u_free", "username": "free", "membership_tier": "free"}


def _client(user):
    from api.routers import agent_presets as mod

    app = FastAPI()
    app.include_router(mod.router)

    async def fake_user():
        return user

    async def fake_session():
        yield None

    app.dependency_overrides[mod.get_current_user] = fake_user
    app.dependency_overrides[mod.get_async_session] = fake_session
    return TestClient(app), mod


def test_templates_list_is_localized_and_has_three():
    client, mod = _client(FREE)
    with patch.object(mod, "agent_presets_enabled", return_value=True):
        res = client.get("/api/agent-presets/templates?language=en")
    assert res.status_code == 200
    tpl = {t["id"]: t for t in res.json()["templates"]}
    assert set(tpl) == {"token_diligence", "tw_macro", "research_markets"}
    assert tpl["token_diligence"]["name"] == "Token diligence: markets + risk"
    assert tpl["token_diligence"]["agent_ids"] == [
        "finance_markets",
        "onchain_security",
    ]
    with patch.object(mod, "agent_presets_enabled", return_value=True):
        zh = client.get("/api/agent-presets/templates").json()["templates"][0]
    assert zh["name"] == "代幣盡調：市場＋風控"


def test_create_from_template_requires_premium_and_creates():
    client, mod = _client(FREE)
    with patch.object(mod, "agent_presets_enabled", return_value=True):
        assert client.post(
            "/api/agent-presets/from-template/token_diligence"
        ).status_code in (402, 403)

    client, mod = _client(PREMIUM)
    created = {}

    async def fake_create(session, **kw):
        created.update(kw)
        return SimpleNamespace(
            preset_id="prst_1",
            user_id=kw["user_id"],
            name=kw["name"],
            mode=kw["mode"],
            agent_ids=kw["agent_ids"],
            analysis_mode=kw["analysis_mode"],
            action_policy=kw["action_policy"],
            capability_overrides={},
            is_default=False,
            config_version=kw["config_version"],
            created_at=None,
            updated_at=None,
        )

    with (
        patch.object(mod, "agent_presets_enabled", return_value=True),
        patch.object(
            mod.agent_presets_repo, "count_presets", new=AsyncMock(return_value=0)
        ),
        # 2026-09-22 起建立前會查有沒有作用中 preset（_should_auto_default）；session 是 None，要 patch
        patch.object(
            mod.agent_presets_repo,
            "get_default_preset",
            new=AsyncMock(return_value=None),
        ),
        patch.object(mod.agent_presets_repo, "create_preset", new=fake_create),
        patch.object(mod, "invalidate_manager_cache") as inv,
    ):
        res = client.post("/api/agent-presets/from-template/tw_macro?language=zh-TW")
    assert res.status_code == 201, res.text
    assert created["name"] == "台股加總經" and created["agent_ids"] == [
        "finance_markets",
        "general_research",
    ]
    assert created["mode"] == "team" and created["action_policy"] == "read_only"
    assert created["is_default"] is True  # 還沒有作用中 preset → 新建的直接作用中
    inv.assert_called_once_with("u_prem")
    assert res.json()["template"]["id"] == "tw_macro"


def test_unknown_template_and_quota():
    client, mod = _client(PREMIUM)
    with patch.object(mod, "agent_presets_enabled", return_value=True):
        assert client.post("/api/agent-presets/from-template/nope").status_code == 404
    with (
        patch.object(mod, "agent_presets_enabled", return_value=True),
        patch.object(
            mod.agent_presets_repo,
            "count_presets",
            new=AsyncMock(return_value=mod.MAX_PRESETS_PER_USER),
        ),
    ):
        assert (
            client.post("/api/agent-presets/from-template/tw_macro").status_code == 409
        )


def test_studio_wiring():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    js = (repo / "web" / "js" / "ai-studio.js").read_text(encoding="utf-8")
    assert (
        "/api/agent-presets/templates" in js and "AIStudioTab.createFromTemplate" in js
    )
    assert "async createFromTemplate(" in js and "_renderTemplatesRow()" in js
