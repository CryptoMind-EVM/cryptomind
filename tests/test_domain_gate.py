"""題型閘門（core/agents/domain_gate.py）：零 LLM 的帳本題決策＋精簡 prompt（2026-10-06）。

背景：金融訊號一律走全工具（86 個）＋完整 system prompt，主呼叫約 2.2 萬 token、首字約 25 秒；
帳本題只需要 5 個工具。守的是：
1. 決策表——該縮的縮、不確定的一律不縮（寧可漏判）；
2. 工具池名稱真的存在於 tool registry，且 flag 一鍵關閉；
3. 帳本版 system prompt 明顯較短、仍含 record_entry／query_ledger 規則與「本輪範圍」出口；
4. claw_loop → SubTask context → base agent 的接線。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agents import domain_gate as dg
from core.agents.agents.cryptomind_agent import CryptoMindAgent

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    monkeypatch.delenv("DOMAIN_GATE_NARROWING", raising=False)


# (query, history, 期望題型)
LEDGER_CASES = [
    ("幫我查看一下帳本目前有什麼紀錄", "", "ledger_terms"),
    ("幫我記一筆今天午餐花了 120 元", "", "ledger_terms"),
    ("這個月支出多少", "", "personal_finance_terms"),
    ("我的收入跟支出統計", "", "personal_finance_terms"),
    ("今天花費多少", "", "personal_finance_terms"),
    ("幫我記一筆午餐 120 台幣", "", "ledger_terms"),
    ("記一筆搭捷運 35 元", "", "ledger_terms"),
    ("帮我看看账本", "", "ledger_terms"),
    ("show my ledger", "", "ledger_terms"),
    ("午餐250", "", "quick_record"),
    ("薪水 50000", "", "quick_record"),
    ("昨天咖啡 85 元", "", "quick_record"),
    # 追問：最近對話真的在談帳本才算
    ("記錄了嗎", "助手: 已記入帳本：支出 120 TWD（food）", "ledger_follow_up"),
    (
        "有記到嗎",
        "用戶: 幫我記一筆午餐\n助手: 確認卡已送出，核准後寫入帳本",
        "ledger_follow_up",
    ),
]

NOT_NARROWED = [
    # 市場／分析／判斷：衝突詞
    "BTC 現在多少",
    "台積電最近怎麼樣",
    # 泛用財務詞沒有「自己的」語境＝市場題
    "台積電收入多少",
    "蘋果公司資本支出多少",
    "NVDA 的營收跟支出",
    "比特幣挖礦收入",
    "AAPL expense ratio",
    "我的帳本裡 BTC 現在價格是多少",
    "幫我分析帳本裡的持倉要不要賣",
    "我看多 BTC，幫我記一筆這個判斷",
    # 記憶／偏好
    "幫我記住我偏好短線",
    "你有記憶我的帳本習慣嗎",
    # 鏈上／錢包
    "查這個錢包地址的交易紀錄",
    # 沒訊號
    "你好",
    "這個平台安全嗎",
    "午餐吃什麼",
    "",
    "   ",
]


@pytest.mark.parametrize("query,history,reason", LEDGER_CASES)
def test_ledger_questions_are_recognised(query, history, reason):
    d = dg.decide_domain(query, history)
    assert d.domain == dg.DOMAIN_LEDGER, (query, d)
    assert d.reason == reason
    assert d.matched


@pytest.mark.parametrize("query", NOT_NARROWED)
def test_everything_uncertain_keeps_the_full_toolset(query):
    d = dg.decide_domain(query, "助手: 已記入帳本")
    assert d.domain is None and not d.matched, (query, d)


def test_follow_up_needs_real_ledger_context():
    assert dg.decide_domain("記錄了嗎", "").domain is None
    assert dg.decide_domain("記錄了嗎", "助手: BTC 今天上漲 3%").domain is None
    # 很久以前談過帳本，現在的追問不該被縮池
    old = "帳本 " + "x" * 2000
    assert dg.decide_domain("記錄了嗎", old).domain is None


def test_long_messages_are_never_narrowed():
    assert dg.decide_domain("幫我查看帳本" + "，然後" * 60).domain is None


def test_flag_off_restores_current_behaviour(monkeypatch):
    for value in ("false", "0", "no", "OFF"):
        monkeypatch.setenv("DOMAIN_GATE_NARROWING", value)
        assert dg.decide_domain("幫我查看一下帳本").domain is None
        assert dg.domain_gate_enabled() is False
    monkeypatch.setenv("DOMAIN_GATE_NARROWING", "true")
    assert dg.decide_domain("幫我查看一下帳本").domain == dg.DOMAIN_LEDGER


def test_tool_pool_is_small_and_names_exist_in_the_registry():
    from core.agents.capability_resolver import build_category_lookup

    names = dg.tool_names_for(dg.DOMAIN_LEDGER)
    assert names == sorted(set(dg.LEDGER_TOOLS) | set(dg.CORE_TOOLS))
    assert len(names) <= 8
    known = set(build_category_lookup())
    assert set(names) <= known, set(names) - known
    # 記／查／改／刪都在
    assert {
        "record_entry",
        "query_ledger",
        "update_ledger_entry",
        "delete_ledger_entry",
    } <= set(names)
    # 不認得的題型不縮
    assert dg.tool_names_for(None) is None
    assert dg.tool_names_for("crypto") is None


def test_gate_vocabulary_is_covered_by_the_finance_veto():
    """閘門的帳本詞都得被 triage 的金融語彙涵蓋，否則會被 T0/T1 當閒聊先攔走。"""
    from core.agents.triage import has_finance_signal

    for term in (
        "帳本",
        "账本",
        "記帳",
        "记账",
        "記一筆",
        "收支",
        "支出",
        "收入",
        "花費",
        "開銷",
    ):
        assert has_finance_signal(term), term


def _agent():
    agent = CryptoMindAgent.__new__(CryptoMindAgent)
    agent.llm = None
    return agent


@pytest.mark.parametrize("language", ["zh-TW", "zh-CN", "en", "ru"])
def test_ledger_prompt_is_much_shorter_but_keeps_the_ledger_rules(language):
    agent = _agent()
    full = agent._get_system_prompt(language)
    ledger = agent._get_system_prompt_for_domain(language, dg.DOMAIN_LEDGER)
    assert len(ledger) < len(full) * 0.6, (len(ledger), len(full))
    # response_quality 內的記帳／查帳規則還在
    assert "record_entry" in ledger and "query_ledger" in ledger
    # 市場分析區塊沒有
    assert "get_crypto_price" not in ledger
    # 誤判的出口（本輪範圍）在
    assert ledger.rstrip().endswith(agent_scope_tail(language))


def agent_scope_tail(language):
    from core.agents.agents import cryptomind_agent as mod

    return mod._LEDGER_SCOPE_NOTE[language]


def test_other_domains_use_the_general_prompt():
    agent = _agent()
    assert agent._get_system_prompt_for_domain(
        "zh-TW", "crypto"
    ) == agent._get_system_prompt("zh-TW")


def test_wiring_claw_loop_to_base_agent():
    claw = (REPO / "core/agents/manager/claw_loop.py").read_text(encoding="utf-8")
    assert "from core.agents.domain_gate import decide_domain, tool_names_for" in claw
    assert '"prompt_domain": _prompt_domain,' in claw
    # Router 已經給了 domain 就不再覆蓋
    assert 'if router_effects.get("router_tool_names") is None:' in claw
    # 閘門自己出意外時 fail-open（全工具），不能弄壞一題
    assert "[DomainGate] skipped (fail-open)" in claw
    assert 'router_effects["router_tool_names"] = _gate_tools' in claw

    base = (REPO / "core/agents/base_react_agent.py").read_text(encoding="utf-8")
    assert '_ctx.get("prompt_domain")' in base
    assert (
        "def _get_system_prompt_for_domain(self, language: str, domain: str) -> str:"
        in base
    )
