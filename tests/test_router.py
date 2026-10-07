"""Router 單元測試 — core/agents/router.py（Model Mixer Step 3）.

釘住企業級護欄：fail-closed（任何失敗→research）、金融訊號硬否決零成本、
T0 詞表＝確定性快取（白名單命中跳過 LLM）、結構化輸出嚴格驗證。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from core.agents.router import (
    DEPTH_ANALYSIS,
    DEPTH_LOOKUP,
    DEPTH_RESEARCH,
    RouteDecision,
    route_query,
)

pytestmark = pytest.mark.unit


def _reply(payload) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False)


def _invoke(payload, *, delay: float = 0.0):
    async def fake(_prompt: str) -> str:
        if delay:
            await asyncio.sleep(delay)
        return _reply(payload)

    return fake


class TestFailClosed:
    async def test_empty_query_never_calls_llm(self):
        async def boom(_p):
            raise AssertionError("should not be called")

        d = await route_query("", boom)
        assert d.depth == DEPTH_RESEARCH
        assert d.source == "fallback"

    async def test_llm_failure_falls_back_to_research(self):
        async def boom(_p):
            raise RuntimeError("provider 500")

        d = await route_query("今天天氣如何", boom)
        assert d.depth == DEPTH_RESEARCH
        assert d.source == "fallback"

    async def test_llm_timeout_falls_back_to_research(self):
        d = await route_query(
            "今天天氣如何", _invoke("{}", delay=0.5), timeout_s=0.05
        )
        assert d.depth == DEPTH_RESEARCH
        assert d.source == "fallback"

    async def test_invalid_json_falls_back_to_research(self):
        d = await route_query("今天天氣如何", _invoke("I think it's a greeting"))
        assert d.depth == DEPTH_RESEARCH
        assert d.source == "fallback"

    async def test_invalid_depth_value_falls_back_to_research(self):
        d = await route_query(
            "隨便一句", _invoke({"domains": [], "depth": "chitchat", "gate": None})
        )
        assert d.depth == DEPTH_RESEARCH
        assert d.source == "fallback"


class TestHardVeto:
    async def test_finance_signal_skips_llm_entirely(self):
        """「BTC 多少」（詞彙表抓不到、ticker 抓得到）→ 硬否決，零 LLM 成本。"""

        async def boom(_p):
            raise AssertionError("finance signal must veto before LLM")

        d = await route_query("BTC 多少", boom)
        assert d.depth == DEPTH_RESEARCH
        assert d.source == "veto"

    async def test_company_price_with_ratio_reaches_router(self):
        """§1 的經典漏判案例：台積電本益比（無數字無語彙無 ticker）→ 進 Router。"""
        d = await route_query(
            "台積電本益比",
            _invoke(
                {"domains": ["finance_markets"], "depth": "lookup", "gate": None}
            ),
        )
        assert d.source == "router"
        assert d.depth == DEPTH_LOOKUP
        assert d.domains == ("finance_markets",)


class TestT0DeterministicCache:
    async def test_whitelist_hit_skips_llm(self):
        """白名單命中＝Router 的確定性子集，零成本直達 lookup（D1=B）。"""

        async def boom(_p):
            raise AssertionError("whitelist hit must skip LLM")

        d = await route_query("你好", boom)
        assert d.depth == DEPTH_LOOKUP
        assert d.source == "t0_cache"

    async def test_greeting_with_finance_tail_is_vetoed(self):
        """開頭寒暄但實質分析請求：白名單完整比對不放行，金融訊號硬否決。"""
        d = await route_query(
            "你好，幫我分析 BTC",
            _invoke(
                {"domains": ["finance_markets"], "depth": "analysis", "gate": None}
            ),
        )
        assert d.source == "veto"
        assert d.depth == DEPTH_RESEARCH


class TestStructuredOutputParsing:
    async def test_plain_json(self):
        d = await route_query(
            "隨便",
            _invoke(
                {"domains": ["finance_markets"], "depth": "analysis", "gate": None}
            ),
        )
        assert d.depth == DEPTH_ANALYSIS
        assert d.gate is None

    async def test_code_fenced_json(self):
        d = await route_query(
            "隨便",
            _invoke('```json\n{"domains": [], "depth": "research", "gate": null}\n```'),
        )
        assert d.depth == DEPTH_RESEARCH
        assert d.source == "router"

    async def test_verbose_reply_with_embedded_json(self):
        d = await route_query(
            "隨便",
            _invoke(
                'Here is my classification: {"domains": [], "depth": "lookup", '
                '"gate": null} hope that helps'
            ),
        )
        assert d.depth == DEPTH_LOOKUP

    async def test_unknown_domains_fail_closed(self):
        d = await route_query(
            "隨便",
            _invoke(
                {
                    "domains": ["finance_markets", "quantum_realm", "ledger_treasury"],
                    "depth": "lookup",
                    "gate": None,
                }
            ),
        )
        assert d.source == "fallback"
        assert not d.is_fast_path

    @pytest.mark.parametrize("fields", [
        {}, {"domains": None}, {"domains": "finance_markets"},
        {"domains": ["finance"]}, {"domains": [12]}, {"domains": [""]},
        {"domains": ["finance_markets", None]},
    ])
    async def test_malformed_domains_never_become_empty_domain_lookup(self, fields):
        d = await route_query(
            "台積電本益比", _invoke({"depth": "lookup", "gate": None, **fields})
        )
        assert d.source == "fallback"
        assert d.depth == DEPTH_RESEARCH
        assert not d.is_fast_path

    async def test_domains_sorted_and_deduped(self):
        d = await route_query(
            "隨便",
            _invoke(
                {
                    "domains": ["general_research", "finance_markets", "finance_markets"],
                    "depth": "research",
                    "gate": None,
                }
            ),
        )
        assert d.domains == ("finance_markets", "general_research")

    async def test_gate_only_accepts_risk_first(self):
        d = await route_query(
            "隨便",
            _invoke({"domains": [], "depth": "lookup", "gate": "risk_first"}),
        )
        assert d.gate == "risk_first"

        d2 = await route_query(
            "隨便", _invoke({"domains": [], "depth": "lookup", "gate": "whatever"})
        )
        assert d2.gate is None

    async def test_gate_may_be_missing(self):
        d = await route_query(
            "隨便", _invoke({"domains": [], "depth": "lookup"})
        )
        assert d.gate is None


class TestDecisionShape:
    def test_is_fast_path_only_for_lookup_without_domains(self):
        assert RouteDecision(
            domains=(), depth=DEPTH_LOOKUP, gate=None, source="t0_cache"
        ).is_fast_path
        assert not RouteDecision(
            domains=(), depth=DEPTH_ANALYSIS, gate=None, source="router"
        ).is_fast_path
        assert not RouteDecision(
            domains=(), depth=DEPTH_RESEARCH, gate=None, source="veto"
        ).is_fast_path

    def test_lookup_with_domain_is_not_fast_path(self):
        """單點資料查詢（lookup＋domain）需要工具——只能走 ReAct。"""
        assert not RouteDecision(
            domains=("finance_markets",), depth=DEPTH_LOOKUP, gate=None, source="router"
        ).is_fast_path

    def test_metrics_dict_shape(self):
        d = RouteDecision(
            domains=("onchain_security",),
            depth=DEPTH_LOOKUP,
            gate="risk_first",
            source="router",
        )
        assert d.metrics_dict() == {
            "depth": "lookup",
            "domains": "onchain_security",
            "gate": "risk_first",
            "source": "router",
            # 成功路由沒有 reason；fallback 才有（2026-09-05）
            "reason": None,
        }

    def test_valid_domains_covers_catalog_profiles(self):
        from core.agents.router import valid_domains

        domains = valid_domains()
        assert {
            "finance_markets",
            "general_research",
            "onchain_security",
            "people_projects",
        } <= set(domains)
