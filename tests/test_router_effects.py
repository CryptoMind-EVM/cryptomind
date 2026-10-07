"""Router 決策 → 推理預算與工具池（2026-09-12）。

守：depth 對應的推理預算只認合法值、預設空；domains 縮池只縮不放、系統工具
與 required_tools 永遠保留、research 不縮；base agent 真的吃 router_tool_names；
claw_loop 真的把兩個鍵放進 SubTask context。
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.agents import router_effects as re_mod
from core.agents.base_react_agent import BaseReActAgent
from core.agents.models import SubTask
from core.agents.tool_registry import ToolMetadata

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


class TestReasoningEffort:
    def test_default_is_untouched(self, monkeypatch):
        for k in (
            "ROUTER_DEPTH_EFFORT_LOOKUP",
            "ROUTER_DEPTH_EFFORT_ANALYSIS",
            "ROUTER_DEPTH_EFFORT_RESEARCH",
        ):
            monkeypatch.delenv(k, raising=False)
        assert re_mod.reasoning_effort_for_depth("lookup") == ""
        assert re_mod.reasoning_effort_for_depth("research") == ""

    def test_env_maps_and_rejects_garbage(self, monkeypatch):
        monkeypatch.setenv("ROUTER_DEPTH_EFFORT_LOOKUP", "low")
        monkeypatch.setenv("ROUTER_DEPTH_EFFORT_ANALYSIS", "turbo")
        assert re_mod.reasoning_effort_for_depth("lookup") == "low"
        assert re_mod.reasoning_effort_for_depth("analysis") == ""
        assert re_mod.reasoning_effort_for_depth(None) == ""


class TestToolPool:
    def test_finance_domain_keeps_market_and_system_tools_drops_onchain(
        self, monkeypatch
    ):
        monkeypatch.delenv("ROUTER_TOOL_POOL_NARROWING", raising=False)
        names = re_mod.router_tool_names(["finance_markets"], "lookup")
        assert names is not None
        assert "get_crypto_price" in names
        assert "load_skill" in names and "web_search" in names, (
            "系統工具（general）永遠保留"
        )
        assert "resolve_symbol" in names, "profile 的 required_tools 要回補"
        assert "check_address_safety" not in names, (
            "onchain 類別不在 finance 的允許清單"
        )

    def test_research_and_unknown_domains_do_not_narrow(self):
        assert re_mod.router_tool_names(["finance_markets"], "research") is None
        assert re_mod.router_tool_names(["no_such_profile"], "lookup") is None
        assert re_mod.router_tool_names([], "lookup") is None

    def test_flag_off(self, monkeypatch):
        monkeypatch.setenv("ROUTER_TOOL_POOL_NARROWING", "false")
        assert re_mod.router_tool_names(["finance_markets"], "lookup") is None

    def test_derive_only_when_router_ran(self, monkeypatch):
        monkeypatch.delenv("ROUTER_TOOL_POOL_NARROWING", raising=False)
        assert re_mod.derive_router_effects(None) == {
            "router_tool_names": None,
            "reasoning_effort": "",
        }
        fallback = {"source": "fallback", "depth": "research", "domains": None}
        assert re_mod.derive_router_effects(fallback)["router_tool_names"] is None
        routed = {"source": "router", "depth": "lookup", "domains": "finance_markets"}
        eff = re_mod.derive_router_effects(routed)
        assert (
            eff["router_tool_names"] and "get_crypto_price" in eff["router_tool_names"]
        )


class TestMiddleware:
    def test_sets_model_settings(self):
        mw = re_mod.ReasoningBudgetMiddleware("low")
        seen = {}

        class _Req:
            model_settings = {"temperature": 0.2}

            def override(self, **kw):
                seen.update(kw)
                return self

        mw.wrap_model_call(_Req(), lambda r: "ok")
        assert seen["model_settings"] == {"temperature": 0.2, "reasoning_effort": "low"}


class _FakeTool:
    name = "fake"
    description = "fake tool"
    args: dict = {}


def _meta(name: str) -> ToolMetadata:
    return ToolMetadata(
        name=name,
        description=name,
        input_schema={},
        handler=_FakeTool(),
        allowed_agents=[],
    )


class _Registry:
    def list_for_agent(self, _name):
        return [
            _meta("web_search"),
            _meta("get_crypto_price"),
            _meta("check_address_safety"),
        ]


class _Agent(BaseReActAgent):
    @property
    def name(self) -> str:
        return "cryptomind"


def _agent():
    return _Agent(llm_client=SimpleNamespace(), tool_registry=_Registry())


def _task(context: dict) -> SubTask:
    return SubTask(step=0, description="q", agent="cryptomind", context=context)


class TestFilterToolMetas:
    def test_router_names_intersect_db_path(self):
        agent = _agent()
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["web_search", "get_crypto_price", "check_address_safety"],
        ):
            names = {
                m.name
                for m in agent._filter_tool_metas(
                    _task(
                        {
                            "router_tool_names": [
                                "web_search",
                                "get_crypto_price",
                                "not_registered",
                            ]
                        }
                    )
                )
            }
        assert names == {"web_search", "get_crypto_price"}

    def test_router_names_cannot_unlock(self):
        agent = _agent()
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["web_search"],
        ):
            names = {
                m.name
                for m in agent._filter_tool_metas(
                    _task({"router_tool_names": ["web_search", "get_crypto_price"]})
                )
            }
        assert names == {"web_search"}

    def test_empty_or_missing_router_names_do_not_filter(self):
        agent = _agent()
        with patch(
            "core.agents.base_react_agent.get_allowed_tools",
            return_value=["web_search", "get_crypto_price"],
        ):
            a = {m.name for m in agent._filter_tool_metas(_task({}))}
            b = {
                m.name
                for m in agent._filter_tool_metas(_task({"router_tool_names": []}))
            }
        assert a == b == {"web_search", "get_crypto_price"}


def test_claw_loop_passes_router_effects_into_subtask_context():
    src = (REPO / "core/agents/manager/claw_loop.py").read_text(encoding="utf-8")
    assert "derive_router_effects(_route_decision)" in src
    ctx = src[
        src.index("task = SubTask(") : src.index(
            "stream_sanitizer = _StreamSanitizer()"
        )
    ]
    assert '"router_tool_names": router_effects.get("router_tool_names")' in ctx
    assert re.search(
        r'"reasoning_effort": router_effects\.get\("reasoning_effort"\)', ctx
    )


def test_execute_streaming_appends_budget_middleware():
    src = (REPO / "core/agents/base_react_agent.py").read_text(encoding="utf-8")
    body = src[src.index("async def execute_streaming") :]
    assert "ReasoningBudgetMiddleware(str(effort))" in body
    assert body.index("middleware = _build_agent_middleware(llm)") < body.index(
        "agent = create_agent("
    )
