"""Preset config 與 agent 工具池整合測試（impl plan Task A2/B5）。

驗證 ``_filter_tool_metas`` 正確消費 ``context["preset_config"]``：
- 只能縮小（preset 外的工具拿不到）
- 未提供 preset_config 時完全走原路徑（向下相容）
- tool_names 為空 list 時保留空交集（與未指定不同）
- **preset 是額外的交集條件，不是 DB 授權層的替代品**：早期版本在 preset
  分支直接 return，跳過 get_allowed_tools，而 resolve_preset_config 的 tier
  層當時是空轉的（所有 seed meta 都寫死 required_tier="free"），兩者疊起來
  讓免費使用者能拿到 premium 工具、Premium 使用者關掉的工具也會復活。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.agents.base_react_agent import BaseReActAgent
from core.agents.models import SubTask
from core.agents.tool_registry import ToolMetadata


class _FakeTool:
    name = "fake"
    description = "fake tool"
    args: dict = {}

    def invoke(self, kwargs):
        return kwargs


def _meta(name: str, tier: str = "free") -> ToolMetadata:
    return ToolMetadata(
        name=name,
        description=name,
        input_schema={},
        handler=_FakeTool(),
        allowed_agents=[],
        required_tier=tier,
    )


class _FakeRegistry:
    def list_for_agent(self, _agent_name):
        return [
            _meta("web_search"),
            _meta("get_crypto_price"),
            _meta("search_projects"),
            _meta("submit_trade", tier="premium"),
        ]


class _DummyAgent(BaseReActAgent):
    @property
    def name(self) -> str:
        return "cryptomind"


def _make_task(context: dict):
    return SubTask(
        step=1,
        description="test",
        agent="cryptomind",
        context=context,
    )


class TestPresetConfigFilter:
    def test_preset_config_narrows_pool(self):
        """preset_config 只保留列出的工具（縮小）。"""
        agent = _DummyAgent(
            llm_client=None, tool_registry=_FakeRegistry(), user_tier="free", user_id="u1"
        )
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["web_search", "get_crypto_price", "search_projects"],
        ):
            metas = agent._get_tool_metas(
                _make_task(
                    {
                        "preset_config": {
                            "tool_names": ["web_search", "search_projects"],
                            "action_policy": "read_only",
                            "config_hash": "abc",
                        }
                    }
                )
            )
        assert sorted(m.name for m in metas) == ["search_projects", "web_search"]

    def test_preset_config_cannot_expand_to_unknown_tools(self):
        """不在 registry 的工具名一律忽略（不可擴權）。"""
        agent = _DummyAgent(
            llm_client=None, tool_registry=_FakeRegistry(), user_tier="free", user_id="u1"
        )
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["web_search", "get_crypto_price"],
        ):
            metas = agent._get_tool_metas(
                _make_task(
                    {
                        "preset_config": {
                            "tool_names": ["totally_unknown_tool"],
                        }
                    }
                )
            )
        assert metas == []

    def test_preset_cannot_unlock_tool_db_did_not_authorize(self):
        """preset 列了 submit_trade，但 DB 授權集沒有 → 仍然拿不到。

        這是 tier 與 user_tool_preferences 的最後一道閘門：preset 只能交集。
        """
        agent = _DummyAgent(
            llm_client=None, tool_registry=_FakeRegistry(), user_tier="free", user_id="u1"
        )
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["web_search", "get_crypto_price"],
        ) as mock_db:
            metas = agent._get_tool_metas(
                _make_task(
                    {
                        "preset_config": {
                            "tool_names": ["web_search", "submit_trade"],
                        }
                    }
                )
            )
        mock_db.assert_called_once()
        assert [m.name for m in metas] == ["web_search"]

    def test_preset_still_narrows_on_db_failure_path(self):
        """DB 掛掉走 tier fallback 時，preset 一樣只縮不放。"""
        agent = _DummyAgent(
            llm_client=None, tool_registry=_FakeRegistry(), user_tier="premium", user_id="u1"
        )
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            side_effect=RuntimeError("db down"),
        ):
            metas = agent._get_tool_metas(
                _make_task({"preset_config": {"tool_names": ["web_search"]}})
            )
        assert [m.name for m in metas] == ["web_search"]

    @pytest.mark.parametrize("db_failure", [False, True])
    def test_empty_tool_names_stays_empty(self, db_failure):
        """Resolver 的空交集不可重新放開工具，DB fallback 也相同。"""
        agent = _DummyAgent(
            llm_client=None, tool_registry=_FakeRegistry(), user_tier="free", user_id="u1"
        )
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["web_search", "get_crypto_price"],
            side_effect=RuntimeError("db down") if db_failure else None,
        ):
            metas = agent._get_tool_metas(
                _make_task({"preset_config": {"tool_names": []}})
            )
        assert metas == []

    def test_resolved_disjoint_pool_stays_empty(self):
        from core.agents.capability_resolver import resolve_preset_config

        config = resolve_preset_config(
            {"agent_ids": ["general_research"], "action_policy": "read_only"},
            user_tier="premium", client_enabled_tools=["get_crypto_price"],
        )
        assert config["tool_names"] == []
        agent = _DummyAgent(
            llm_client=None, tool_registry=_FakeRegistry(), user_tier="premium", user_id="u1"
        )
        with patch("core.agents.base_react_agent.get_allowed_tools",
                   return_value=["web_search", "get_crypto_price"]):
            assert agent._get_tool_metas(_make_task({
                "preset_config": config, "enabled_tools": ["get_crypto_price"],
            })) == []

    def test_no_preset_config_keeps_legacy_path(self):
        """未提供 preset_config → 原本 DB 路徑不變（向下相容）。"""
        agent = _DummyAgent(
            llm_client=None, tool_registry=_FakeRegistry(), user_tier="premium", user_id="u1"
        )
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["web_search", "submit_trade"],
        ) as mock_db:
            metas = agent._get_tool_metas(_make_task({}))
        mock_db.assert_called_once()
        assert sorted(m.name for m in metas) == ["submit_trade", "web_search"]

    def test_premium_tool_excluded_for_free_user_via_resolver(self):
        """端到端：resolver 產生 people_projects preset_config（無 submit_trade）。"""
        from core.agents.capability_resolver import resolve_preset_config

        config = resolve_preset_config(
            {"agent_ids": ["people_projects"], "action_policy": "read_only"},
            user_tier="free",
        )
        agent = _DummyAgent(
            llm_client=None, tool_registry=_FakeRegistry(), user_tier="free", user_id="u1"
        )
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["web_search", "get_crypto_price", "search_projects"],
        ):
            metas = agent._get_tool_metas(_make_task({"preset_config": config}))
        names = [m.name for m in metas]
        assert "submit_trade" not in names
        assert "search_projects" in names

    def test_resolver_tier_layer_is_not_inert_for_free_users(self):
        """resolve_preset_config 的 tier 層必須真的過濾。

        早期版本把每個 seed meta 寫死 required_tier="free"，第 4 層形同不存在，
        免費使用者的 tool_names 會混進 premium 工具（實測 finance_markets
        64 個裡有 31 個）。這裡直接對 _TOOLS_SEED 的 tier_required 對帳。
        """
        from core.agents.capability_resolver import resolve_preset_config
        from core.database.tools import _TOOLS_SEED

        premium_ids = {
            e["tool_id"] for e in _TOOLS_SEED if e.get("tier_required") != "free"
        }
        assert premium_ids, "_TOOLS_SEED 應該有非 free 工具，否則這個測試沒有意義"

        for agent_id in ("general_research", "finance_markets", "onchain_security"):
            free = resolve_preset_config(
                {"agent_ids": [agent_id], "action_policy": "read_only"},
                user_tier="free",
            )
            assert not (set(free["tool_names"]) & premium_ids), (
                f"{agent_id}: 免費使用者的 preset 工具池混進了 premium 工具"
            )
            premium = resolve_preset_config(
                {"agent_ids": [agent_id], "action_policy": "read_only"},
                user_tier="premium",
            )
            # Premium 不受影響（修法只縮免費、不動付費）
            assert set(free["tool_names"]) <= set(premium["tool_names"])
