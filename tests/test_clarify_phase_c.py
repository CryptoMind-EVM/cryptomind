"""
Phase C（should_clarify）誤觸發與失敗卡片回歸測試。

H2：used_tools=[] 時，query 含任何大寫縮寫（ETF／GDP／SQL…）而回答含「不確定」，
    或回答含「正在分析／fetching data」這類字眼，整個好答案就被換成罐頭釐清文字。
M8：agent 因後端錯誤（金鑰無效／額度／連線）失敗時，被當成「模型回空」彈出
    「我不太確定你問的…」釐清卡。
"""

from __future__ import annotations

import re
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agents.clarification import (
    _build_basic_clarification,
    _detect_query_ticker,
    build_multi_market_clarification,
    is_multi_market_trigger,
    should_clarify,
)

pytestmark = pytest.mark.unit

_CJK_RE = re.compile(r"[一-鿿]")
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")


# ============================================================================
# H2：通用縮寫不是代號
# ============================================================================


@pytest.mark.parametrize(
    "query",
    [
        "ETF 跟定存哪個划算？我月薪六萬",
        "What is GDP?",
        "我想學 SQL 怎麼開始",
        "JSON 怎麼解析比較好",
        "IRA 跟 401k 哪個好",
        "APR 跟 APY 差多少",
        "RSI 跟 MACD 哪個比較準",
        "ESG 基金值得買嗎",
        "FED 升息對股市影響",
    ],
)
def test_detect_query_ticker_skips_generic_acronyms(query):
    assert _detect_query_ticker(query) is None


def test_detect_query_ticker_still_finds_real_ticker_next_to_acronym():
    assert _detect_query_ticker("ETF 還是 AKE 比較好") == "AKE"


def test_h2_etf_vs_deposit_good_answer_is_not_replaced():
    """線上重現：好答案裡一句「我不確定你的風險承受度」不該整個換成罐頭。"""
    result = should_clarify(
        response="我不確定你的風險承受度，但月薪六萬的話，通常建議先備足六個月生活費…",
        used_tools=[],
        query="ETF 跟定存哪個划算？我月薪六萬",
    )
    assert result is None


def test_h2_what_is_gdp_fake_action_word_does_not_trigger_sentinel():
    result = should_clarify(
        response="Fetching data about GDP is not needed to answer this: GDP is the "
        "total value of goods and services produced in a country.",
        used_tools=[],
        query="What is GDP?",
    )
    assert result is None


# ============================================================================
# H2：query 要有金融標的語境
# ============================================================================


def test_non_financial_query_with_unknown_caps_word_does_not_trigger():
    result = should_clarify(
        response="這個我不確定，建議你看官方文件。",
        used_tools=[],
        query="AWS 要怎麼學？",
    )
    assert result is None


def test_bare_ticker_query_still_triggers():
    """使用者只打一個代號，模型說查無 → 仍是該釐清的情境。"""
    result = should_clarify(response="查無 SNXX 此幣。", used_tools=[], query="SNXX")
    assert result is not None
    assert "SNXX" in result


def test_what_is_unknown_ticker_still_triggers():
    result = should_clarify(
        response="找不到 XYZABC", used_tools=[], query="XYZABC 是什麼"
    )
    assert result is not None


# ============================================================================
# H2：不確定語要貼近該代號，或整則回答很短才算「放棄了」
# ============================================================================

_LONG_GOOD_ANSWER = (
    "SNXX 是一檔槓桿型 ETF，追蹤特定個股的兩倍日報酬，持有越久損耗越明顯，"
    "不適合長期持有，通常只用於短線交易。這類商品的報酬路徑與標的並不線性，"
    "震盪行情下即使標的不漲不跌，淨值也會慢慢縮水。要評估它是否適合你，"
    "我不確定你的風險承受度與投資期間，所以以下先給通用原則：先確認你能否承受"
    "單日大幅波動，再決定部位大小，並設定停損與檢視頻率，避免情緒化操作。"
    "另外要留意費用率與追蹤誤差，這類商品的內扣費用通常高於一般指數型基金，"
    "長期持有的成本會被放大，建議只拿閒置資金、而且能盯盤的部位來操作。"
)


def test_long_answer_with_unrelated_uncertainty_is_not_replaced():
    assert len(_LONG_GOOD_ANSWER) > 200
    result = should_clarify(
        response=_LONG_GOOD_ANSWER, used_tools=[], query="SNXX 值得買嗎"
    )
    assert result is None


def test_long_answer_with_uncertainty_about_the_ticker_still_triggers():
    answer = _LONG_GOOD_ANSWER.replace(
        "我不確定你的風險承受度與投資期間",
        "我不確定 SNXX 在哪個市場掛牌",
    )
    result = should_clarify(response=answer, used_tools=[], query="SNXX 值得買嗎")
    assert result is not None


def test_short_give_up_answer_still_triggers():
    result = should_clarify(
        response="抱歉，我不確定這是什麼。", used_tools=[], query="SNXX 值得買嗎"
    )
    assert result is not None


def test_answer_with_concrete_numbers_is_an_answer_not_a_give_up():
    result = should_clarify(
        response="SNXX 現價 $13.20，單日漲 2.3%，但我不確定它的長期走勢。",
        used_tools=[],
        query="SNXX 值得買嗎",
    )
    assert result is None


# ============================================================================
# H2：_FAKE_ACTION_RE 路徑要求 response 沒有實質內容
# ============================================================================


def test_fake_action_word_inside_long_substantive_answer_does_not_trigger():
    answer = (
        "AKE 的基本面可以從三個角度看：團隊背景、代幣經濟與流動性。"
        "正在分析這類小型標的時，最重要的是先確認合約地址與流通量，"
        "再看交易所是否有足夠深度，避免滑價；其次觀察持幣集中度與解鎖時程，"
        "若前十大地址持有超過六成，價格就容易被少數人左右。最後才是技術面，"
        "小型標的的指標常常失真，只能當輔助。整體來說風險偏高，部位要小，"
        "也要預設自己可能全數虧損，並且避免使用槓桿。這些都是通用原則，"
        "實際決策還需要結合你自己的資金規劃與風險承受度，不要被短期價格牽著走。"
        "如果你願意補充預計持有多久、可承受的最大虧損比例、以及目前的資產配置，"
        "我可以再幫你把部位大小與分批進場的節奏拆開來討論，讓風險更可控一些。"
        "此外，小型標的的公告與社群消息常常比價格先動，建議把官方公告、"
        "合約審計報告與主要交易所的上架資訊都先看過，再決定要不要進場。"
    )
    assert len(answer) > 300
    result = should_clarify(response=answer, used_tools=[], query="AKE 值得買嗎")
    assert result is None


def test_fake_action_placeholder_with_numbers_does_not_trigger():
    result = should_clarify(
        response="AKE 目前 $0.52（+3.1%），正在計算 RSI…",
        used_tools=[],
        query="AKE 現在值得買嗎",
    )
    assert result is None


def test_fake_action_empty_shell_still_triggers_sentinel():
    result = should_clarify(
        response="📊 AKE 即時行情概況\n當前價格：(正在獲取即時價格...)\n市值：(正在獲取市值數據...)",
        used_tools=[],
        query="AKE 現在值得買嗎",
    )
    assert is_multi_market_trigger(result)


# ============================================================================
# 罐頭文字依語言輸出
# ============================================================================


def test_basic_clarification_default_is_zh_tw_unchanged():
    msg = _build_basic_clarification("SNXX")
    assert "您查詢的「SNXX」" in msg
    assert "加密貨幣" in msg and "美股" in msg


def test_basic_clarification_zh_cn_is_simplified():
    msg = _build_basic_clarification("SNXX", "zh-CN")
    assert "SNXX" in msg
    assert "您查询的" in msg and "加密货币" in msg
    assert "加密貨幣" not in msg


def test_basic_clarification_en_has_no_chinese():
    msg = _build_basic_clarification("SNXX", "en")
    assert "SNXX" in msg
    assert not _CJK_RE.search(msg)
    assert "crypto" in msg.lower()


def test_basic_clarification_ru_is_russian():
    msg = _build_basic_clarification("SNXX", "ru")
    assert "SNXX" in msg
    assert _CYRILLIC_RE.search(msg)
    assert not _CJK_RE.search(msg)


@pytest.mark.parametrize("language", ["en", "ru", "zh-CN"])
def test_should_clarify_passes_language_to_message(language):
    result = should_clarify(
        response="I cannot find SNXX in any database.",
        used_tools=[],
        query="SNXX 值得買嗎",
        language=language,
    )
    assert result is not None
    assert result == _build_basic_clarification("SNXX", language)


def test_multi_market_empty_candidates_fallback_uses_language():
    msg = build_multi_market_clarification(
        "AKE", '{"query": "AKE", "candidates": []}', language="en"
    )
    assert not _CJK_RE.search(msg)


# ============================================================================
# claw_loop 節點：Phase C 呼叫區與 _agent_failed 分支（M8）
# ============================================================================


def _node_manager(agent_result, language="zh-TW"):
    from core.agents.manager._main import ManagerAgent

    manager = ManagerAgent(
        llm_client=MagicMock(),
        agent_registry=MagicMock(),
        tool_registry=MagicMock(),
        user_id="test-user",
        session_id="test-session",
    )
    manager.language = language
    agent = MagicMock()
    agent._filter_tool_metas.return_value = []
    agent.execute_streaming = AsyncMock(return_value=agent_result)
    manager.agent_registry.get.return_value = agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)
    return manager, agent


def _state(query):
    return {"query": query, "history": "", "session_id": "test-session"}


async def test_node_good_answer_with_generic_acronym_is_kept():
    """H2 端到端：節點回的 final_response 就是模型的答案，不是罐頭卡。"""
    from core.agents.models import AgentResult

    answer = "我不確定你的風險承受度，但月薪六萬的話，通常建議先備足六個月生活費。"
    manager, _ = _node_manager(
        AgentResult(
            success=True,
            message=answer,
            agent_name="cryptomind",
            data={"used_tools": []},
        )
    )
    out = await manager._claw_loop_node(_state("ETF 跟定存哪個划算？我月薪六萬"))
    assert out["final_response"] == answer
    assert not out.get("_needs_clarification")


async def test_node_phase_c_message_follows_user_language():
    from core.agents.models import AgentResult

    manager, _ = _node_manager(
        AgentResult(
            success=True,
            message="I cannot find SNXX anywhere.",
            agent_name="cryptomind",
            data={"used_tools": []},
        ),
        language="en",
    )
    out = await manager._claw_loop_node(_state("SNXX 值得買嗎"))
    assert out["_needs_clarification"] is True
    assert "SNXX" in out["final_response"]
    assert not _CJK_RE.search(out["final_response"])


async def test_node_multi_market_lookup_runs_off_the_event_loop():
    """同步的 resolve_symbol_all_markets_sync 不能在 async node 裡阻塞 event loop。"""
    from core.agents.manager.claw_loop import ClawLoopMixin
    from core.agents.models import AgentResult

    loop_thread = threading.get_ident()
    seen = {}

    def _fake_build(self, ticker_or_query, language):
        seen["thread"] = threading.get_ident()
        seen["args"] = (ticker_or_query, language)
        return "候選清單"

    manager, _ = _node_manager(
        AgentResult(
            success=True,
            message="📊 AKE 即時行情\n當前價格：(正在獲取即時價格...)",
            agent_name="cryptomind",
            data={"used_tools": []},
        )
    )
    with patch.object(ClawLoopMixin, "_build_multi_market_question", _fake_build):
        out = await manager._claw_loop_node(_state("AKE 現在值得買嗎"))

    assert out["final_response"] == "候選清單"
    assert seen["args"] == ("AKE", "zh-TW")
    assert seen["thread"] != loop_thread


def test_multi_market_fallback_message_follows_language():
    """候選查詢失敗退回基本卡時，也要依語言。"""
    from core.agents.manager._main import ManagerAgent

    manager = ManagerAgent(
        llm_client=MagicMock(),
        agent_registry=MagicMock(),
        tool_registry=MagicMock(),
        user_id="u",
        session_id="s",
    )
    with patch(
        "core.tools.multi_market_resolver.resolve_symbol_all_markets_sync",
        side_effect=RuntimeError("probe down"),
    ):
        msg = manager._build_multi_market_question("AKE", "en")
    assert "AKE" in msg
    assert not _CJK_RE.search(msg)


_VAGUE_QUERY = "我月薪六萬要怎麼理財規劃"


async def test_m8_backend_error_does_not_pop_vague_clarify_card():
    """agent 因金鑰無效失敗 → 顯示錯誤訊息，不彈「你問的不清楚」卡。"""
    from core.agents.models import AgentResult

    error_text = "⚠️ API Key 無效或已過期，請到設定頁面更新金鑰。"
    manager, _ = _node_manager(
        AgentResult(success=False, message=error_text, agent_name="cryptomind")
    )
    vague = AsyncMock(return_value={"type": "clarify", "question": "q", "options": []})
    interrupt = MagicMock(side_effect=AssertionError("不該彈釐清卡"))
    with (
        patch("core.agents.clarification.should_clarify_vague_query", vague),
        patch("langgraph.types.interrupt", interrupt),
    ):
        out = await manager._claw_loop_node(_state(_VAGUE_QUERY))

    vague.assert_not_awaited()
    interrupt.assert_not_called()
    assert out["final_response"] == error_text


async def test_m8_model_gave_up_empty_still_reaches_vague_clarify():
    """模型回空（base_react_agent 塞 empty_response_message）仍走 Phase D——不能退化。"""
    from core.agents.base_react_agent import empty_response_message
    from core.agents.models import AgentResult

    manager, _ = _node_manager(
        AgentResult(
            success=False,
            message=empty_response_message("zh-TW"),
            agent_name="cryptomind",
        )
    )
    vague = AsyncMock(return_value=None)
    with patch("core.agents.clarification.should_clarify_vague_query", vague):
        await manager._claw_loop_node(_state(_VAGUE_QUERY))

    vague.assert_awaited_once()


async def test_m8_failed_agent_error_text_is_not_treated_as_give_up_by_phase_c():
    """錯誤訊息裡剛好有「找不到」，也不能被 Phase C 當成模型放棄而換成釐清卡。"""
    from core.agents.models import AgentResult

    error_text = "找不到可用的資料來源，請稍後再試。"
    manager, _ = _node_manager(
        AgentResult(success=False, message=error_text, agent_name="cryptomind")
    )
    out = await manager._claw_loop_node(_state("SNXX 值得買嗎"))

    assert out["final_response"] == error_text
    assert not out.get("_needs_clarification")
