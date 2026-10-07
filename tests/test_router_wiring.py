"""Router 接線測試 — claw_loop._resolve_fast_route ＋ RunMetrics 欄位（Step 3）.

釘住 flag 語義：ROUTER_ENABLED=off → 與 #617 完全相同的 T0→T1 路徑；
on → 單一 Router 呼叫（T0 快取與硬否決在 route_query 內），行為面只有
depth=lookup 觸發 fast path，decision 進 metrics。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.agents.manager.claw_loop import _resolve_fast_route, log_run_metrics
from core.agents.router import route_query
from core.agents.triage import SIMPLE_QA

pytestmark = pytest.mark.unit


def _router_reply(payload):
    async def fake(_prompt):
        return json.dumps(payload, ensure_ascii=False)

    return fake


def _t1_reply(answer):
    async def fake(_prompt):
        return answer

    return fake


class TestFlagOff:
    async def test_t0_path_unchanged(self):
        with patch("core.agents.manager.claw_loop.router_enabled", return_value=False):
            route, decision = await _resolve_fast_route(
                "你好", _router_reply({}), _t1_reply(SIMPLE_QA)
            )
        assert route == "T0"
        assert decision is None

    async def test_t1_path_unchanged(self):
        with patch("core.agents.manager.claw_loop.router_enabled", return_value=False):
            route, decision = await _resolve_fast_route(
                "你可以幫我做什麼", _router_reply({}), _t1_reply(SIMPLE_QA)
            )
        assert route == "T1"
        assert decision is None

    async def test_finance_signal_goes_react(self):
        with patch("core.agents.manager.claw_loop.router_enabled", return_value=False):
            route, decision = await _resolve_fast_route(
                "BTC 多少", _router_reply({}), _t1_reply(SIMPLE_QA)
            )
        assert route is None
        assert decision is None


class TestFlagOn:
    async def test_router_lookup_no_domain_hits_fast_path(self):
        """lookup＋無 domain（純寒暄/meta）＝fast path 唯一觸發組合。"""
        with patch("core.agents.manager.claw_loop.router_enabled", return_value=True):
            route, decision = await _resolve_fast_route(
                "這個平台安全嗎",
                _router_reply({"domains": [], "depth": "lookup", "gate": None}),
                _t1_reply(SIMPLE_QA),
            )
        assert route == "router"
        assert decision == {
            "depth": "lookup",
            "domains": None,
            "gate": None,
            "source": "router",
            # 成功路由沒有 reason；fallback 才有（2026-09-05）
            "reason": None,
        }

    async def test_router_lookup_with_domain_goes_react(self):
        """lookup＋有 domain（單點資料查詢）→ fast path 無工具答不了，
        必須走 ReAct（工具在領域軸）。"""
        with patch("core.agents.manager.claw_loop.router_enabled", return_value=True):
            route, decision = await _resolve_fast_route(
                "台積電本益比",
                _router_reply(
                    {"domains": ["finance_markets"], "depth": "lookup", "gate": None}
                ),
                _t1_reply(SIMPLE_QA),
            )
        assert route is None
        assert decision["depth"] == "lookup"
        assert decision["domains"] == "finance_markets"

    async def test_router_analysis_goes_react_with_decision(self):
        """非 lookup：不攔，但 decision 仍要進 metrics（eval／Step 5 基礎）。"""
        with patch("core.agents.manager.claw_loop.router_enabled", return_value=True):
            route, decision = await _resolve_fast_route(
                "哪家電動車品牌比較好",
                _router_reply(
                    {"domains": ["general_research"], "depth": "analysis", "gate": None}
                ),
                _t1_reply(SIMPLE_QA),
            )
        assert route is None
        assert decision["depth"] == "analysis"

    async def test_router_failure_falls_back_to_react(self):
        async def boom(_p):
            raise RuntimeError("provider down")

        with patch("core.agents.manager.claw_loop.router_enabled", return_value=True):
            route, decision = await _resolve_fast_route(
                "隨便一句", boom, _t1_reply(SIMPLE_QA)
            )
        assert route is None
        assert decision["depth"] == "research"

    async def test_t1_not_called_when_router_on(self):
        """flag on 時不得付兩次 LLM：T1 呼叫端不該被碰到。"""

        async def t1_boom(_p):
            raise AssertionError("T1 must not be called when router is enabled")

        with patch("core.agents.manager.claw_loop.router_enabled", return_value=True):
            route, _ = await _resolve_fast_route(
                "隨便一句", _router_reply({"domains": [], "depth": "lookup"}), t1_boom
            )
        assert route == "router"


def _run_metrics_line(caplog) -> str:
    """只挑 RunMetrics 那一行——log_run_metrics 之後還會寫 run_metrics_store，
    那條路徑（快取／Redis）的 log 不能擠掉要驗的行。"""
    lines = [r.getMessage() for r in caplog.records if "[RunMetrics]" in r.getMessage()]
    assert len(lines) == 1, f"每題恰好一行 RunMetrics：{caplog.messages}"
    return lines[0]


class TestRunMetricsFields:
    def test_new_fields_appended_with_dash_defaults(self, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="API"):
            log_run_metrics(
                route="claw_loop", elapsed_s=1.0, tool_calls=0, response_chars=10
            )
        line = _run_metrics_line(caplog)
        assert "depth=-" in line and "domains=-" in line and "gate=-" in line
        # 舊欄位順序不變（Step 1 格式相容）
        head = line.split(" model=")[0]
        assert "route=claw_loop elapsed_s=1.00" in head

    def test_decision_fields_rendered(self, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="API"):
            log_run_metrics(
                route="fast_path_router",
                elapsed_s=0.5,
                tool_calls=0,
                response_chars=5,
                depth="lookup",
                domains="finance_markets",
                gate="risk_first",
            )
        line = _run_metrics_line(caplog)
        assert "depth=lookup" in line
        assert "domains=finance_markets" in line
        assert "gate=risk_first" in line


class TestRouteQueryUsedByWiring:
    async def test_wiring_uses_router_module(self):
        """_resolve_fast_route 的 flag-on 分支就是 route_query（單一真相來源）。"""
        with patch(
            "core.agents.manager.claw_loop.route_query", wraps=route_query
        ) as spy, patch(
            "core.agents.manager.claw_loop.router_enabled", return_value=True
        ):
            await _resolve_fast_route(
                "你好", _router_reply({}), _t1_reply(SIMPLE_QA)
            )
        spy.assert_awaited_once()
