"""claw_loop Phase B（數字驗證）的閘門與 nudge。

背景（H5）：Phase B 原本只要用過任何工具、回答含 % / 萬 / $ 就把數字拿去比對工具輸出。
個人理財試算（「月薪 6 萬…儲蓄 17%…NT$7,500」）搭配 resolve_symbol 整批被標可疑，
整輪 agent 重跑，而且 nudge 是寫死的中文。

這裡跑整個 ``_claw_loop_node``（fake agent），確認：
1. 沒有市場資料工具輸出（只用了 resolve_symbol 等）→ 不驗證、不重跑
2. 使用者自己講的數字與其簡單運算 → 不重跑
3. 真正捏造的價格 → 重跑一次，nudge 走 i18n（依使用者語言）
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agents.manager._main import ManagerAgent
from core.agents.models import AgentResult

pytestmark = pytest.mark.unit

_TW_PRICE = '{"symbol": "2330", "price": 2290, "change_pct": 1.2}'


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """claw_loop／LLM 重試路徑的 backoff 不要真的睡（同 test_claw_direct_mode）。"""
    import core.agents.manager.claw_loop as claw_loop_mod

    monkeypatch.setattr(claw_loop_mod.asyncio, "sleep", AsyncMock())


def _make_manager(results, language="zh-TW"):
    manager = ManagerAgent(
        llm_client=MagicMock(),
        agent_registry=MagicMock(),
        tool_registry=MagicMock(),
        user_id="test-user",
        session_id="test-session",
    )
    manager.language = language
    agent = MagicMock()
    agent.execute_streaming = AsyncMock(side_effect=results)
    manager.agent_registry.get.return_value = agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)
    return manager, agent


def _result(message, used_tools, market_outputs, other_outputs=()):
    """other_outputs：resolve_symbol／時間錨點這類非市場工具的輸出（只進 tool_outputs）。"""
    return AgentResult(
        success=True,
        message=message,
        agent_name="cryptomind",
        data={
            "used_tools": used_tools,
            "tool_outputs": [*other_outputs, *market_outputs],
            "market_tool_outputs": list(market_outputs),
        },
    )


def _run(manager, query, history=""):
    state = {"query": query, "history": history, "session_id": "test-session"}
    return asyncio.run(manager._claw_loop_node(state))


def test_budget_answer_with_only_resolve_symbol_is_not_rerun():
    """H5 主案例：只用了 resolve_symbol、沒有任何市場資料 → 試算答案原樣放行。"""
    answer = (
        "月薪 6 萬（NT$60,000）建議：房租 NT$15,000、儲蓄 17%（NT$10,000）、"
        "每月彈性支出 NT$7,500。"
    )
    resolved = _result(
        answer,
        ["resolve_symbol", "get_current_time_taipei"],
        [],
        other_outputs=[
            '{"symbol": "2330", "market": "tw"}',
            "[REF: 現在 2026-10-06 14:00]",
        ],
    )
    manager, agent = _make_manager([resolved])
    result = _run(manager, "月薪 6 萬，台積電要不要買？幫我抓每月預算")
    assert agent.execute_streaming.await_count == 1
    assert "NT$7,500" in result["final_response"]


def test_user_numbers_and_arithmetic_with_market_data_are_not_rerun():
    """有市場資料時，使用者的數字與其簡單運算不算幻覺。"""
    answer = "台積電現價 NT$2,290。你的 6 萬（NT$60,000）預算約可買 26 股，房租 NT$15,000 佔 25%。"
    manager, agent = _make_manager([_result(answer, ["tw_stock_price"], [_TW_PRICE])])
    _run(manager, "月薪 6 萬，房租 1.5 萬，台積電現在多少？")
    assert agent.execute_streaming.await_count == 1


def test_fabricated_price_triggers_one_rerun_with_localized_nudge():
    """真正捏造的股價仍要抓到：重跑一次，nudge 依使用者語言（英文使用者不看到中文）。"""
    bad = _result("TSMC trades at NT$2,890 today.", ["tw_stock_price"], [_TW_PRICE])
    good = _result("TSMC trades at NT$2,290 today.", ["tw_stock_price"], [_TW_PRICE])
    manager, agent = _make_manager([bad, good], language="en")
    result = _run(manager, "how is TSMC doing")
    assert agent.execute_streaming.await_count == 2
    rerun_task = agent.execute_streaming.await_args_list[1].args[0]
    assert "2,890" in rerun_task.description  # 告訴模型哪個數字有問題
    assert "無法從工具結果驗證" not in rerun_task.description
    assert "verified" in rerun_task.description  # i18n en 版
    assert "2,290" in result["final_response"]


def test_nudge_follows_language_zh_tw():
    bad = _result("台積電現價 NT$2,890", ["tw_stock_price"], [_TW_PRICE])
    good = _result("台積電現價 NT$2,290", ["tw_stock_price"], [_TW_PRICE])
    manager, agent = _make_manager([bad, good], language="zh-TW")
    _run(manager, "台積電現在多少")
    rerun_task = agent.execute_streaming.await_args_list[1].args[0]
    assert "無法從工具結果驗證" in rerun_task.description


def test_missing_market_tool_outputs_key_skips_verification():
    """agent 沒回報 market_tool_outputs（舊格式／其他 agent）→ 保守放行，不重跑。"""
    result = AgentResult(
        success=True,
        message="台積電現價 NT$2,890",
        agent_name="cryptomind",
        data={"used_tools": ["tw_stock_price"], "tool_outputs": [_TW_PRICE]},
    )
    manager, agent = _make_manager([result])
    _run(manager, "台積電現在多少")
    assert agent.execute_streaming.await_count == 1
