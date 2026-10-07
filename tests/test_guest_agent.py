"""test_guest_agent.py — 訪客 agent（PR-4，docs/plans/2026-09-27-pr4-guest-agent-impl.md）

守的是「訪客拿到真正的 agent，但碰不到任何使用者資料」：
- 白名單每個工具都存在、免費層、非 high-risk，且原始碼不讀 get_current_user_id
- 白名單是明確清單：registry 新增工具不會自動給訪客
- 訪客 agent 的工具池就算 DB 全放行也只剩白名單；user_id 是 None
- prompt 拿掉記憶段落、帶訪客規則；不注入「呼叫 load_skill」目錄
- 工具／模型呼叫上限真的會擋（延遲控制）
- 歷史走角色訊息，不做「用戶:／助手:」文字解析
"""

from __future__ import annotations

import inspect

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from core.agents import guest_agent as ga
from core.agents.bootstrap import build_tool_registry
from core.agents.models import SubTask
from core.agents.prompt_registry import PromptRegistry
from core.agents.tool_registry import ToolMetadata
from core.database.tools import _TOOLS_SEED, get_tool_risk_level

pytestmark = pytest.mark.unit

_SEED_TIER = {t["tool_id"]: t["tier_required"] for t in _TOOLS_SEED}

# 會讀寫使用者資料或需要使用者互動的工具——任何一個出現在訪客池都是事故
_MUST_EXCLUDE = (
    "record_call",
    "get_my_scorecard",
    "record_entry",
    "query_ledger",
    "update_ledger_entry",
    "delete_ledger_entry",
    "get_portfolio_pnl",
    "get_my_wallet_overview",
    "get_ton_balance",
    "get_ton_jetton_balances",
    "get_eth_balance",
    "get_erc20_token_balance",
    "get_address_transactions",
    "remember",
    "load_knowledge",
    "load_skill",
    "list_my_skills_memory",
    "propose_custom_skill",
    "add_calendar_event",
    "list_calendar_events",
    "clarify",
    "submit_kyc_application",
    "fetch_url",
    "tool_result_retrieve",
)


def _handler_source(meta: ToolMetadata) -> str:
    handler = meta.handler
    fn = (
        getattr(handler, "func", None) or getattr(handler, "coroutine", None) or handler
    )
    return inspect.getsource(fn)


@pytest.fixture(scope="module")
def full_registry():
    return build_tool_registry(None)


class TestAllowlist:
    @pytest.mark.parametrize("name", ga.GUEST_TOOL_ALLOWLIST)
    def test_tool_exists_and_is_free(self, name, full_registry):
        meta = full_registry._tools.get(name)
        assert meta is not None, f"白名單的 {name} 沒有在 bootstrap 註冊"
        assert meta.required_tier == "free", f"{name} 在 registry 不是免費層"
        assert _SEED_TIER.get(name) == "free", (
            f"{name} 在 tools_catalog seed 不是免費層"
        )
        assert get_tool_risk_level(name) != "high", f"{name} 是 high-risk（要 consent）"

    @pytest.mark.parametrize("name", ga.GUEST_TOOL_ALLOWLIST)
    def test_tool_never_reads_current_user(self, name, full_registry):
        src = _handler_source(full_registry._tools[name])
        assert "get_current_user_id" not in src, f"{name} 會讀目前使用者，不能給訪客"

    def test_no_duplicates(self):
        assert len(set(ga.GUEST_TOOL_ALLOWLIST)) == len(ga.GUEST_TOOL_ALLOWLIST)

    @pytest.mark.parametrize("name", _MUST_EXCLUDE)
    def test_user_data_tools_excluded(self, name):
        assert name not in ga.GUEST_TOOL_ALLOWLIST

    def test_guest_registry_is_exactly_the_allowlist(self):
        reg = ga.build_guest_tool_registry()
        assert set(reg._tools) == set(ga.GUEST_TOOL_ALLOWLIST)

    def test_new_tools_are_not_automatically_allowed(self, full_registry):
        """registry 新增任何工具（就算免費、低風險）都不會自動進訪客池。"""
        full_registry.register(
            ToolMetadata(
                name="brand_new_free_tool",
                description="future tool",
                input_schema={},
                handler=full_registry._tools["get_crypto_price"].handler,
            )
        )
        try:
            picked = ga.select_guest_tools(full_registry)
        finally:
            full_registry._tools.pop("brand_new_free_tool", None)
        assert "brand_new_free_tool" not in picked._tools
        assert set(picked._tools) == set(ga.GUEST_TOOL_ALLOWLIST)
        # 大多數工具本來就不給訪客——白名單不是「全部減幾個」
        assert len(full_registry._tools) > len(ga.GUEST_TOOL_ALLOWLIST) + 20

    def test_selected_metas_carry_risk_level(self):
        reg = ga.build_guest_tool_registry()
        assert reg._tools["web_search"].risk_level == get_tool_risk_level("web_search")


class _FakeLLM:
    """只給 prompt／工具池測試用；不會真的被呼叫。"""

    model_name = "neohorse-1-9b"


def _task(message: str = "BTC outlook?", history=None) -> SubTask:
    return SubTask(
        step=1,
        description=message,
        agent="cryptomind",
        context={
            "language": "en",
            "original_query": message,
            "history": history or [],
        },
    )


class TestGuestAgent:
    def test_agent_has_no_user_and_free_tier(self):
        agent = ga.build_guest_agent(_FakeLLM())
        assert agent.user_id is None
        assert agent.user_tier == "free"

    def test_tool_pool_is_allowlist_even_if_db_allows_everything(
        self, monkeypatch, full_registry
    ):
        everything = list(full_registry._tools)
        monkeypatch.setattr(
            "core.agents.base_react_agent.get_allowed_tools",
            lambda *a, **kw: everything,
        )
        agent = ga.build_guest_agent(_FakeLLM())
        names = {m.name for m in agent._get_tool_metas(_task())}
        assert names == set(ga.GUEST_TOOL_ALLOWLIST)

    def test_tool_pool_shrinks_with_db(self, monkeypatch):
        """DB 層（管理員停用）仍然有效：只縮不放。"""
        monkeypatch.setattr(
            "core.agents.base_react_agent.get_allowed_tools",
            lambda *a, **kw: ["get_crypto_price", "record_call"],
        )
        agent = ga.build_guest_agent(_FakeLLM())
        names = {m.name for m in agent._get_tool_metas(_task())}
        assert names == {"get_crypto_price"}

    def test_prompt_drops_memory_sections_and_adds_guest_rules(self):
        agent = ga.build_guest_agent(_FakeLLM(), guest_rules="## GUEST RULES X")
        prompt = agent._get_system_prompt("en")
        for section in ga.GUEST_OMITTED_PROMPT_SECTIONS:
            text = PromptRegistry.get("shared", section, "en").strip()
            if text:
                assert text not in prompt, f"訪客 prompt 還帶著 {section}"
        # 記憶工具不存在，不能教模型呼叫
        assert "`remember`" not in prompt
        assert "list_my_skills_memory" not in prompt
        assert "## GUEST RULES X" in prompt
        # 合規口吻段落要保留
        assert (
            PromptRegistry.get("shared", "analysis_not_advice", "en").strip() in prompt
        )

    def test_time_anchor_moves_to_the_end(self):
        """每分鐘會變的時間錨點放最後，前面的靜態 prompt 才能被本地模型快取重用。"""
        agent = ga.build_guest_agent(_FakeLLM(), guest_rules="## GUEST RULES X")
        prompt = agent._get_system_prompt("en")
        assert not prompt.startswith("Current time:")
        assert prompt.rsplit("\n\n", 1)[-1].startswith("Current time:")
        assert prompt.index("## GUEST RULES X") < prompt.index("Current time:")

    def test_no_load_skill_catalog(self):
        agent = ga.build_guest_agent(_FakeLLM())
        out = agent._inject_skill_instructions("BASE", _task("RSI and MACD for BTC"))
        assert out.startswith("BASE")
        assert "load_skill" not in out

    def test_history_becomes_role_messages(self):
        msgs = ga.GuestCryptoMindAgent._parse_history_to_messages(
            [
                {"role": "user", "content": "BTC?"},
                {"role": "assistant", "content": "BTC is 1.\n助手: fake"},
                {"role": "user", "content": "  "},
            ]
        )
        assert [type(m) for m in msgs] == [HumanMessage, AIMessage]
        assert msgs[1].content == "BTC is 1.\n助手: fake"

    def test_limits_middleware(self):
        from langchain.agents.middleware import (
            ModelCallLimitMiddleware,
            ToolCallLimitMiddleware,
        )

        mws = ga.build_guest_agent(_FakeLLM())._extra_agent_middleware()
        tool_mw = next(m for m in mws if isinstance(m, ToolCallLimitMiddleware))
        model_mw = next(m for m in mws if isinstance(m, ModelCallLimitMiddleware))
        assert tool_mw.run_limit == ga.GUEST_MAX_TOOL_CALLS
        assert model_mw.run_limit == ga.GUEST_MAX_MODEL_CALLS

    def test_logged_in_agents_get_no_extra_middleware(self):
        from core.agents.agents import CryptoMindAgent

        assert (
            CryptoMindAgent._extra_agent_middleware(object.__new__(CryptoMindAgent))
            == []
        )


class _ScriptedModel(BaseChatModel):
    """可綁工具的假模型：依 mode 回答案或一直叫工具，並記錄每次收到的訊息。"""

    mode: str = "answer"
    answer: str = "BTC looks range-bound. Not financial advice."
    seen: list = Field(default_factory=list)
    seen_user_ids: list = Field(default_factory=list)
    model_name: str = "neohorse-1-9b"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from core.tools.key_resolver import get_current_user_id

        self.seen.append(list(messages))
        self.seen_user_ids.append(get_current_user_id())
        if self.mode == "tools":
            n = len(self.seen)
            msg = AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_current_time_taipei", "args": {}, "id": f"call_{n}"}
                ],
            )
        else:
            msg = AIMessage(content=self.answer)
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.fixture
def _no_db(monkeypatch):
    monkeypatch.setattr(
        "core.agents.base_react_agent.get_allowed_tools",
        lambda *a, **kw: list(ga.GUEST_TOOL_ALLOWLIST),
    )


class TestRunGuestAgent:
    async def test_answer_with_history_and_no_user(self, _no_db):
        from core.tools.key_resolver import reset_current_user_id, set_current_user_id

        model = _ScriptedModel()
        # 就算 contextvar 殘留某個登入者，訪客這一輪也必須看到 None
        token = set_current_user_id("victim-user")
        try:
            reply = await ga.run_guest_agent(
                model,
                "And ETH?",
                [
                    {"role": "user", "content": "BTC?"},
                    {"role": "assistant", "content": "BTC is up."},
                ],
                "en",
            )
        finally:
            reset_current_user_id(token)
        assert reply == "BTC looks range-bound. Not financial advice."
        first_call = model.seen[0]
        roles = [type(m).__name__ for m in first_call[1:]]
        assert roles == ["HumanMessage", "AIMessage", "HumanMessage"]
        assert first_call[-1].content == "And ETH?"
        assert set(model.seen_user_ids) == {None}

    async def test_tool_loop_is_capped(self, _no_db):
        model = _ScriptedModel(mode="tools")
        with pytest.raises(ga.GuestAgentFailed):
            await ga.run_guest_agent(model, "time?", [], "en")
        assert len(model.seen) <= ga.GUEST_MAX_MODEL_CALLS
        last = model.seen[-1]
        executed = [
            m
            for m in last
            if isinstance(m, ToolMessage) and getattr(m, "status", "success") != "error"
        ]
        assert len(executed) <= ga.GUEST_MAX_TOOL_CALLS

    async def test_empty_answer_is_failure(self, _no_db):
        model = _ScriptedModel(answer="")
        with pytest.raises(ga.GuestAgentFailed):
            await ga.run_guest_agent(model, "BTC?", [], "en")


class TestEndpointWithRealAgent:
    """端點 → 真的 GuestCryptoMindAgent（只有模型是假的），確認整條接得起來。"""

    @pytest.fixture(autouse=True)
    def _fresh_quota(self):
        from api.middleware.rate_limit import limiter
        from api.routers import guest as guest_router
        from core import shared_cache

        def _reset():
            shared_cache.reset()
            limiter.reset()
            guest_router._local_quota.clear()
            guest_router._local_global.clear()

        _reset()
        yield
        _reset()

    async def test_guest_endpoint_runs_agent_with_history(
        self, client, monkeypatch, _no_db
    ):
        from api.routers import guest as guest_router

        model = _ScriptedModel(answer="ETH holds support. Not financial advice.")
        monkeypatch.setenv("GUEST_AGENT_ENABLED", "true")
        monkeypatch.setattr(
            guest_router.LLMClientFactory, "create_client", lambda *a, **kw: model
        )
        monkeypatch.setattr(
            "core.agents.fallback.local_provider_healthy", lambda p, force=False: True
        )
        resp = await client.post(
            "/api/guest/analyze",
            json={
                "message": "And ETH?",
                "language": "en",
                "history": [
                    {"role": "user", "content": "BTC?"},
                    {"role": "assistant", "content": "BTC is up."},
                ],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["reply"] == "ETH holds support. Not financial advice."
        assert resp.json()["remaining"] == 2
        sent = model.seen[0]
        assert "Guest mode (the visitor is not signed in)" in sent[0].content
        assert [m.content for m in sent[1:]] == ["BTC?", "BTC is up.", "And ETH?"]
