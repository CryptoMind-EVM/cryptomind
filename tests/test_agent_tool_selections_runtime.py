"""Mixer Step 2 執行期接線測試。

- ``load_agent_tool_selections``：Premium gate ＋ user_agent_configs.tools →
  {agent_id: 白名單}（docs/plans/2026-09-03-model-mixer-step2-tools.md §5）。
- ``analysis._resolve_agent_preset_config``：勾選傳入 resolve_preset_config。
- ``agent_presets.get_resolved_capabilities``：預覽與執行期同一套勾選（不說謊）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routers.agent_configs import load_agent_tool_selections
from api.routers.analysis import _resolve_agent_preset_config

pytestmark = pytest.mark.unit

_PREMIUM = {"user_id": "u_prem", "username": "prem", "membership_tier": "premium"}
_FREE = {"user_id": "u_free", "username": "free", "membership_tier": "free"}


def _config_row(agent_id, tools, model_selection=None):
    return SimpleNamespace(
        agent_id=agent_id, tools=tools, model_selection=model_selection
    )


def _preset(agent_ids, preset_id="prst_abc"):
    return SimpleNamespace(
        preset_id=preset_id,
        agent_ids=agent_ids,
        mode="single" if len(agent_ids) == 1 else "team",
        analysis_mode="quick",
        action_policy="read_only",
        capability_overrides={},
    )


def _resolved_config():
    return {
        "tool_names": ["web_search"],
        "profiles": ["general_research"],
        "action_policy": "read_only",
        "analysis_mode": "quick",
        "config_hash": "hash123",
        "warnings": [],
        "catalog_version": "test",
    }


class TestLoadAgentToolSelections:
    async def test_premium_with_tools_returns_selections(self):
        rows = [
            _config_row("general_research", ["web_search"]),
            _config_row("onchain_security", []),  # 空清單 = 未設定 → 不帶
        ]
        with patch(
            "core.orm.agent_configs_repo.agent_configs_repo.list_configs",
            new=AsyncMock(return_value=rows),
        ):
            result = await load_agent_tool_selections(None, _PREMIUM, "p_test")
        assert result == {"general_research": ["web_search"]}

    async def test_free_user_returns_none(self):
        """寫入端 Premium-only → 讀取端重驗（殘留設定不得生效）。"""
        with patch(
            "core.orm.agent_configs_repo.agent_configs_repo.list_configs",
            new=AsyncMock(),
        ) as mock_list:
            result = await load_agent_tool_selections(None, _FREE, "p_test")
        assert result is None
        mock_list.assert_not_awaited()

    async def test_all_empty_returns_none(self):
        with patch(
            "core.orm.agent_configs_repo.agent_configs_repo.list_configs",
            new=AsyncMock(return_value=[_config_row("general_research", [])]),
        ):
            result = await load_agent_tool_selections(None, _PREMIUM, "p_test")
        assert result is None

    async def test_repo_error_degrades_to_none(self):
        with patch(
            "core.orm.agent_configs_repo.agent_configs_repo.list_configs",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            result = await load_agent_tool_selections(None, _PREMIUM, "p_test")
        assert result is None


class TestAnalysisWiring:
    async def test_premium_selections_passed_to_resolver(self):
        with patch(
            "core.feature_flags.agent_presets_enabled", return_value=True
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_default_preset",
            new=AsyncMock(return_value=_preset(["general_research"])),
        ), patch(
            "core.orm.agent_configs_repo.agent_configs_repo.list_configs",
            new=AsyncMock(
                return_value=[_config_row("general_research", ["web_search"])]
            ),
        ), patch(
            "core.agents.capability_resolver.resolve_preset_config",
            new=MagicMock(return_value=_resolved_config()),
        ) as mock_resolve:
            config = await _resolve_agent_preset_config(
                None, _PREMIUM, None, None
            )
        assert config is not None
        assert mock_resolve.call_args.kwargs["agent_tool_selections"] == {
            "general_research": ["web_search"]
        }

    async def test_free_user_selections_not_passed(self):
        with patch(
            "core.feature_flags.agent_presets_enabled", return_value=True
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_default_preset",
            new=AsyncMock(return_value=_preset(["general_research"])),
        ), patch(
            "core.orm.agent_configs_repo.agent_configs_repo.list_configs",
            new=AsyncMock(),
        ) as mock_list, patch(
            "core.agents.capability_resolver.resolve_preset_config",
            new=MagicMock(return_value=_resolved_config()),
        ) as mock_resolve:
            await _resolve_agent_preset_config(None, _FREE, None, None)
        mock_list.assert_not_awaited()
        assert (
            mock_resolve.call_args.kwargs.get("agent_tool_selections") is None
        )

    async def test_repo_error_degrades_without_selections(self):
        """勾選讀取失敗 → 照常解析 preset（無勾選），不阻斷聊天。"""
        with patch(
            "core.feature_flags.agent_presets_enabled", return_value=True
        ), patch(
            "core.orm.agent_presets_repo.agent_presets_repo.get_default_preset",
            new=AsyncMock(return_value=_preset(["general_research"])),
        ), patch(
            "core.orm.agent_configs_repo.agent_configs_repo.list_configs",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ), patch(
            "core.agents.capability_resolver.resolve_preset_config",
            new=MagicMock(return_value=_resolved_config()),
        ) as mock_resolve:
            config = await _resolve_agent_preset_config(
                None, _PREMIUM, None, None
            )
        assert config is not None
        assert mock_resolve.call_args.kwargs.get("agent_tool_selections") is None


class TestPresetsPreviewWiring:
    """resolved-capabilities 預覽與 activate 摘要必須與執行期同一套勾選。"""

    def _make_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.routers import agent_presets as ap_module

        app = FastAPI()
        app.include_router(ap_module.router)

        async def fake_user():
            return _PREMIUM

        async def fake_session():
            yield None

        app.dependency_overrides[ap_module.get_current_user] = fake_user
        app.dependency_overrides[ap_module.get_async_session] = fake_session
        return app, TestClient(app)

    def test_resolved_capabilities_passes_selections(self):
        app, client = self._make_client()
        try:
            with patch(
                "api.routers.agent_presets.agent_presets_enabled",
                return_value=True,
            ), patch(
                "core.orm.agent_presets_repo.agent_presets_repo.get_preset",
                new=AsyncMock(return_value=_preset(["general_research"])),
            ), patch(
                "core.orm.agent_configs_repo.agent_configs_repo.list_configs",
                new=AsyncMock(
                    return_value=[
                        _config_row("general_research", ["web_search"])
                    ]
                ),
            ), patch(
                "api.routers.agent_presets.resolve_preset_config",
                new=MagicMock(return_value=_resolved_config()),
            ) as mock_resolve:
                resp = client.get("/api/agent-presets/prst_abc/resolved-capabilities")
            assert resp.status_code == 200
            assert (
                mock_resolve.call_args.kwargs["agent_tool_selections"]
                == {"general_research": ["web_search"]}
            )
        finally:
            app.dependency_overrides.clear()

    def test_activate_summary_passes_selections(self):
        """啟用摘要的 tool_count 是使用者啟用後立刻看到的數字，接線要釘住。"""
        app, client = self._make_client()
        try:
            with patch(
                "api.routers.agent_presets.agent_presets_enabled",
                return_value=True,
            ), patch(
                "core.orm.agent_presets_repo.agent_presets_repo.set_default",
                new=AsyncMock(return_value=True),
            ), patch(
                "core.orm.agent_presets_repo.agent_presets_repo.get_preset",
                new=AsyncMock(return_value=_preset(["general_research"])),
            ), patch(
                "core.orm.agent_configs_repo.agent_configs_repo.list_configs",
                new=AsyncMock(
                    return_value=[
                        _config_row("general_research", ["web_search"])
                    ]
                ),
            ), patch(
                "api.routers.agent_presets.resolve_preset_config",
                new=MagicMock(return_value=_resolved_config()),
            ) as mock_resolve, patch(
                "api.routers.agent_presets.invalidate_manager_cache"
            ):
                resp = client.post("/api/agent-presets/prst_abc/activate")
            assert resp.status_code == 200
            assert (
                mock_resolve.call_args.kwargs["agent_tool_selections"]
                == {"general_research": ["web_search"]}
            )
        finally:
            app.dependency_overrides.clear()


class TestScopeCannotSilentlyWiden:
    """c044：載入器沒有 preset 就不套用，不得退回使用者層全域設定。

    在 c044 之前它讀的是 (user_id) 範圍——所有 preset 共用一份。退化回去
    的話症狀是「A preset 的工具設定在 B preset 生效」，不噴錯、只在行為上
    看得出來，正是最難發現的那種。
    """

    async def test_missing_preset_returns_none_not_global(self):
        from unittest.mock import AsyncMock, patch

        from api.routers.agent_configs import load_agent_tool_selections

        with patch(
            "core.orm.agent_configs_repo.agent_configs_repo.list_configs",
            new=AsyncMock(side_effect=AssertionError("不該在沒有 preset 時查 DB")),
        ):
            assert await load_agent_tool_selections(None, _PREMIUM, "") is None
            assert await load_agent_tool_selections(None, _PREMIUM, None) is None

    async def test_repo_is_queried_by_preset_not_user(self):
        from unittest.mock import AsyncMock, patch

        from api.routers.agent_configs import load_agent_tool_selections

        spy = AsyncMock(return_value=[])
        with patch("core.orm.agent_configs_repo.agent_configs_repo.list_configs", new=spy):
            await load_agent_tool_selections(None, _PREMIUM, "p_scope")
        spy.assert_awaited_once_with(None, "p_scope")
