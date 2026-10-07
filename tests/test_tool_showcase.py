"""工具櫥窗（core/agents/tool_showcase.py）：市場題只展示代表工具，要用再開（2026-10-06）。

背景：金融題第一次主呼叫要讀 86 個工具定義（約 1.3 萬 token），大部分與當題無關。守的是：
1. 決策表（evals/tool_showcase_cases.jsonl）——該縮的縮、動作／個人／太雜／認不出的一律不縮（寧可漏判）；
2. 工具名稱真的存在於 _TOOLS_SEED、帳本／個人工具不會混進市場家族、flag 一鍵關閉；
3. 展示層語意：縮窄時只給代表工具＋出口；呼叫過出口後改回完整清單（用真的 create_agent 驗）；
4. claw_loop → SubTask context → base agent 的接線，以及不與帳本閘門／Router 疊加。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool as lc_tool

from core.agents import tool_showcase as ts

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
CASES = [
    json.loads(line)
    for line in (REPO / "evals" / "tool_showcase_cases.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv("TOOL_SHOWCASE_NARROWING", raising=False)


# ── 1. 決策表 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES, ids=[c["q"][:24] or "(empty)" for c in CASES])
def test_decision_table(case):
    decision = ts.decide_showcase(case["q"])
    assert sorted(decision.families) == sorted(case["expect"]), decision.reason


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "FALSE"])
def test_flag_off_means_no_narrowing(monkeypatch, raw):
    monkeypatch.setenv("TOOL_SHOWCASE_NARROWING", raw)
    assert not ts.decide_showcase("台積電現在多少").matched


def test_flag_defaults_on_and_junk_value_keeps_it_on(monkeypatch):
    assert ts.showcase_enabled() is True
    monkeypatch.setenv("TOOL_SHOWCASE_NARROWING", "maybe")
    assert ts.showcase_enabled() is True


def test_too_many_families_is_not_narrowed():
    d = ts.decide_showcase("台股美股比特幣黃金匯率今天一次告訴我")
    assert d.families == () and d.reason == "too_many_families"


def test_personal_or_action_wording_beats_market_words():
    for q in ("我持有的 BTC 今天漲跌", "提醒我看台積電財報", "幫我記下看多黃金", "我的美股帳本"):
        d = ts.decide_showcase(q)
        assert d.families == () and d.reason == "action_or_personal", q


# ── 2. 工具池名稱與內容 ───────────────────────────────────────────────────────


def _seed_ids() -> set:
    from core.database.tools import _TOOLS_SEED

    return {e["tool_id"] for e in _TOOLS_SEED}


def test_every_family_and_core_tool_exists_in_seed():
    seed = _seed_ids()
    names = set(ts.CORE_TOOLS)
    for tools in ts.FAMILY_TOOLS.values():
        names.update(tools)
    assert not names - seed, f"櫥窗引用了不存在的工具：{sorted(names - seed)}"


def test_ledger_and_personal_tools_never_in_market_families():
    personal = {
        "record_entry", "query_ledger", "update_ledger_entry", "delete_ledger_entry",
        "get_portfolio_pnl", "get_my_wallet_overview", "get_my_scorecard", "record_call",
        "add_calendar_event", "list_calendar_events", "submit_kyc_application",
    }
    shown = set(ts.CORE_TOOLS)
    for tools in ts.FAMILY_TOOLS.values():
        shown.update(tools)
    assert not shown & personal


def test_core_tools_always_included_and_unknown_family_gives_none():
    names = ts.tool_names_for(["tw_stock"])
    assert set(ts.CORE_TOOLS) <= set(names) and "tw_stock_price" in names
    assert "us_stock_price" not in names
    assert ts.tool_names_for([]) is None
    assert ts.tool_names_for(["nope"]) is None


def test_union_of_families_is_union_of_tools():
    names = set(ts.tool_names_for(["tw_stock", "us_stock"]))
    assert "tw_stock_price" in names and "us_stock_price" in names


def test_narrowed_pool_is_much_smaller_than_the_full_pool():
    """櫥窗的意義：展示的工具少一大半（全工具約 87 個）。"""
    for fam in ts.FAMILY_TOOLS:
        assert len(ts.tool_names_for([fam])) <= 32, fam


# ── 3. 展示層語意（真的 create_agent＋假模型）──────────────────────────────────────


def _mk_tool(name: str):
    @lc_tool(name)
    def _t(q: str = "") -> str:
        """test tool"""
        return f"{name} ok"

    return _t


class _Recorder(BaseChatModel):
    """腳本化的假模型：記下每次呼叫被綁定的工具名，依腳本回 AIMessage。"""

    script: List[AIMessage]
    seen: List[List[str]] = []
    _i: int = 0

    @property
    def _llm_type(self) -> str:
        return "recorder"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        names = [getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None) for t in tools]
        self.seen.append(sorted(n for n in names if n))
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # noqa: ANN001
        msg = self.script[min(self._i, len(self.script) - 1)]
        object.__setattr__(self, "_i", self._i + 1)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _call(name: str, call_id: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _agent(script, shown, all_names):
    model = _Recorder(script=script, seen=[])
    tools = [_mk_tool(n) for n in all_names] + [ts.build_request_more_tools_tool()]
    agent = create_agent(model=model, tools=tools, middleware=[ts.ToolShowcaseMiddleware(shown)])
    return agent, model


def test_only_showcase_tools_plus_exit_are_shown_until_exit_is_called():
    all_names = ["tw_price", "us_price", "web_search", "clarify"]
    agent, model = _agent(
        [AIMessage(content="好的，這是答案")], shown={"tw_price", "web_search", "clarify"}, all_names=all_names
    )
    out = agent.invoke({"messages": [HumanMessage(content="台積電")]})
    assert model.seen[0] == sorted(["tw_price", "web_search", "clarify", ts.REQUEST_MORE_TOOLS])
    assert out["messages"][-1].content == "好的，這是答案"


def test_calling_the_exit_opens_the_full_list_for_every_later_call():
    all_names = ["tw_price", "us_price", "web_search", "clarify"]
    agent, model = _agent(
        [
            _call(ts.REQUEST_MORE_TOOLS, "c1", reason="需要美股"),
            _call("us_price", "c2", q="TSM"),
            AIMessage(content="TSM 報價如下"),
        ],
        shown={"tw_price", "web_search", "clarify"},
        all_names=all_names,
    )
    out = agent.invoke({"messages": [HumanMessage(content="台積電 ADR")]})
    # 第 1 次：縮窄＋出口；第 2、3 次：完整清單（不含出口，避免來回呼叫）
    assert model.seen[0] == sorted(["tw_price", "web_search", "clarify", ts.REQUEST_MORE_TOOLS])
    assert model.seen[1] == sorted(all_names)
    assert model.seen[2] == sorted(all_names)
    # 櫥窗外的工具真的被執行了（它一直在 ToolNode，只是第一輪沒展示）
    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert [m.name for m in tool_msgs] == [ts.REQUEST_MORE_TOOLS, "us_price"]
    assert out["messages"][-1].content == "TSM 報價如下"


def test_expanded_is_detected_from_history_so_nudge_retries_stay_open():
    msgs = [HumanMessage(content="q"), ToolMessage(content="ok", name=ts.REQUEST_MORE_TOOLS, tool_call_id="x")]
    assert ts.ToolShowcaseMiddleware.expanded(msgs) is True
    assert ts.ToolShowcaseMiddleware.expanded([HumanMessage(content="q")]) is False
    assert ts.ToolShowcaseMiddleware.expanded(None) is False


def test_provider_dict_tools_without_a_name_are_kept():
    class Req:
        def __init__(self, tools, messages):
            self.tools, self.messages = tools, messages

        def override(self, **kw):
            return Req(kw.get("tools", self.tools), self.messages)

    mw = ts.ToolShowcaseMiddleware({"a"})
    req = Req([_mk_tool("a"), _mk_tool("b"), {"type": "web_search_preview"}], [])
    shown = mw._request(req).tools
    assert [getattr(t, "name", None) or t.get("type") for t in shown] == ["a", "web_search_preview"]


def test_exit_tool_is_a_harmless_langchain_tool():
    t = ts.build_request_more_tools_tool()
    assert t.name == ts.REQUEST_MORE_TOOLS
    assert "完整工具清單" in t.invoke({"reason": "缺美股"})
    # 描述要教模型「有合適工具時不要呼叫」，否則小模型會每題都呼叫、白白多一次完整讀取
    assert "不要呼叫" in (t.description or "")


# ── 4. 接線 ──────────────────────────────────────────────────────────────────


def test_claw_loop_passes_showcase_names_and_does_not_stack_on_other_gates():
    src = (REPO / "core" / "agents" / "manager" / "claw_loop.py").read_text(encoding="utf-8")
    assert '"tool_showcase_names": _showcase_names' in src
    # 只有 Router 沒縮池、帳本閘門沒命中時才疊櫥窗
    assert 'router_effects.get("router_tool_names") is None and not _prompt_domain' in src


def test_base_agent_installs_middleware_and_exit_tool_only_when_narrowing():
    src = (REPO / "core" / "agents" / "base_react_agent.py").read_text(encoding="utf-8")
    assert 'context.get("tool_showcase_names")' in src
    assert "ToolShowcaseMiddleware(_show_handlers | _always)" in src
    # 展示集合涵蓋全部工具時不必縮（也就不掛出口）
    assert "len(_show_handlers) >= len(tools)" in src


def test_sanitizer_knows_the_exit_tool_name():
    from core.agents import tool_name_registry as reg

    assert ts.REQUEST_MORE_TOOLS in reg._FALLBACK_NAMES
    # bootstrap 注入動態清單時也要帶上它（它不在 tool registry）
    src = (REPO / "core" / "agents" / "bootstrap.py").read_text(encoding="utf-8")
    assert '"request_more_tools", "read_more_result"' in src


def test_web_search_is_not_a_core_tool_so_the_model_uses_the_exit():
    """2026-10-06 本機真模型實測：櫥窗缺工具時，只要 web_search 還在，模型就不呼叫出口、改用網搜硬湊
    （一題 18 次 LLM 呼叫）；拿掉後會老實呼叫 request_more_tools，開完整清單再用 web_search。"""
    assert "web_search" not in ts.CORE_TOOLS and "fetch_url" not in ts.CORE_TOOLS
    for tools in ts.FAMILY_TOOLS.values():
        assert "web_search" not in tools and "fetch_url" not in tools
