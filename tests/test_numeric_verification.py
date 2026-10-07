"""
Numeric Verification 測試 — 防止金融數字幻覺。

核心案例：SNXX 對話中「2.22% 跌幅」被幻覺成「660 點盤中波動」，
後續整段分析建立在幻覺上。
"""
from __future__ import annotations

import pytest

from core.agents.verification import (
    _is_simple_derivation,
    extract_financial_numbers,
    verify_numeric_consistency,
)

# ============================================================================
# 1. extract_financial_numbers
# ============================================================================


def test_extract_currency():
    nums = extract_financial_numbers("BTC 現價 $64,000.50")
    assert len(nums) == 1
    assert nums[0].value == 64000.50


def test_extract_percentage():
    nums = extract_financial_numbers("24h 漲跌 +1.62%")
    assert len(nums) == 1
    assert abs(nums[0].value - 1.62) < 0.01


def test_extract_negative_percentage():
    nums = extract_financial_numbers("下跌 -2.22%")
    assert len(nums) == 1
    assert abs(nums[0].value - (-2.22)) < 0.01


def test_extract_points():
    nums = extract_financial_numbers("盤中波動 660 點")
    assert len(nums) == 1
    assert nums[0].value == 660


def test_extract_nt_dollar():
    nums = extract_financial_numbers("台積電 NT$2,290")
    assert len(nums) == 1
    assert nums[0].value == 2290


def test_extract_market_cap():
    nums = extract_financial_numbers("市值約 1.28 兆美元")
    assert len(nums) == 1
    assert abs(nums[0].value - 1.28) < 0.01


def test_extract_multiple_numbers():
    nums = extract_financial_numbers("BTC $64000，ETH $1845，漲跌 +1.62%")
    assert len(nums) == 3


def test_extract_empty_text():
    assert extract_financial_numbers("") == []
    assert extract_financial_numbers(None) == []  # type: ignore


def test_no_generic_numbers_extracted():
    """一般數字（如「3 個建議」「Step 1」）不該被當金融數字。"""
    nums = extract_financial_numbers("以下 3 個建議，Step 1 是買入")
    assert len(nums) == 0


# ============================================================================
# 2. _is_simple_derivation
# ============================================================================


def test_direct_match_passes():
    assert _is_simple_derivation(64000.0, [64000, 1300, 51]) is True


def test_subtraction_passes():
    """13.50 - 13.20 = 0.30 是合法推論。"""
    assert _is_simple_derivation(0.30, [13.50, 13.20]) is True


def test_addition_passes():
    assert _is_simple_derivation(26.70, [13.50, 13.20]) is True


def test_division_passes():
    assert _is_simple_derivation(50.0, [100, 2]) is True


def test_percentage_derivation_passes():
    """13.5 * 2.22 / 100 = 0.2997 ≈ 0.30（百分比換算）。"""
    assert _is_simple_derivation(0.2997, [13.5, 2.22]) is True


def test_no_match_fails():
    """660 在 [64000, 1300, 51] 中找不到來源。"""
    assert _is_simple_derivation(660.0, [64000, 1300, 51]) is False


def test_empty_source_fails():
    assert _is_simple_derivation(660.0, []) is False


# ============================================================================
# 3. verify_numeric_consistency — 核心案例
# ============================================================================


def test_snxx_660_hallucination_detected():
    """SNXX 660 點幻覺的核心回歸測試。

    背景：nemotron 把「2.22% 跌幅」幻覺成「660 點盤中波動」，
    後續整段分析建立在幻覺上。
    """
    response = (
        "SNXX 目前價格為 $13.20（-2.22%），盤中曾出現超過 660 點的波動。"
    )
    tool_outputs = [
        "price: 13.20, prev_close: 13.50, change_pct: -2.22, RSI: 29.6"
    ]
    result = verify_numeric_consistency(response, tool_outputs)
    assert result.has_hallucination_risk
    suspicious_values = [n.value for n in result.suspicious_numbers]
    assert 660 in suspicious_values, f"660 應被標記可疑，實際: {suspicious_values}"


def test_all_numbers_sourced_passes():
    response = "BTC $64000，漲跌 +1.62%"
    tool_outputs = ["price: 64000, change: 1.62%"]
    result = verify_numeric_consistency(response, tool_outputs)
    assert result.passed is True
    assert result.has_hallucination_risk is False


def test_no_numbers_in_response_passes():
    result = verify_numeric_consistency("你好", ["any output"])
    assert result.passed is True


def test_no_tool_output_all_suspicious():
    """tool 沒回任何數字，但 LLM 回應有金融數字 → 全部可疑。"""
    response = "BTC $64000，漲跌 +1.62%"
    result = verify_numeric_consistency(response, ["no data"])
    assert result.has_hallucination_risk
    assert len(result.suspicious_numbers) == 2


def test_small_numbers_skipped():
    """< 1 的數字（如 RSI 0.5）預設跳過。"""
    response = "RSI 為 0.45，MACD 0.02"
    tool_outputs = ["RSI: 0.55"]
    result = verify_numeric_consistency(response, tool_outputs)
    # 小數字被跳過，不標記可疑
    assert result.passed is True


def test_derivation_from_tool_numbers_passes():
    """LLM 從 tool 數字做簡單換算 → 合法。"""
    response = "跌了 $0.30"
    tool_outputs = ["prev: 13.50, current: 13.20"]
    result = verify_numeric_consistency(response, tool_outputs)
    assert result.passed is True  # 0.30 = 13.50 - 13.20


def test_multiple_tool_outputs_merged():
    """多個 tool 輸出的數字合併檢查。"""
    response = "BTC $64000，ETH $1845"
    tool_outputs = [
        "BTC price: 64000",
        "ETH price: 1845",
    ]
    result = verify_numeric_consistency(response, tool_outputs)
    assert result.passed is True


def test_mixed_real_and_hallucinated():
    """部分真實 + 部分幻覺。"""
    response = "BTC $64000（真實），但盤中波動 9999 點（幻覺）"
    tool_outputs = ["price: 64000"]
    result = verify_numeric_consistency(response, tool_outputs)
    assert result.has_hallucination_risk
    suspicious_values = [n.value for n in result.suspicious_numbers]
    assert 9999 in suspicious_values
    # 64000 不在可疑清單
    assert 64000 not in suspicious_values


# ============================================================================
# 4. 個人理財／使用者自己的數字不該被當成幻覺（H5）
#
# 背景：Phase B 原本只要用過任何工具、回答含 % / 萬 / $ 就拿去比對工具輸出，
# 預算試算（「月薪 6 萬…儲蓄 17%…NT$7,500」）整批被標可疑，整輪 agent 重跑。
# ============================================================================

_PRICE_OUTPUT = '{"symbol": "BTC", "price": 64000, "change_pct": 1.62}'


def _suspicious_raw(result):
    return [n.raw for n in result.suspicious_numbers]


def test_user_stated_numbers_are_allowed():
    """使用者自己講的數字（含「6 萬＝60000」換算）不是幻覺。"""
    result = verify_numeric_consistency(
        "以月薪 6 萬（NT$60,000）估算",
        [_PRICE_OUTPUT],
        user_context="我月薪 6 萬，幫我抓預算",
    )
    assert result.passed, _suspicious_raw(result)


@pytest.mark.parametrize(
    "context,answer",
    [
        ("我有 10 萬想買 BTC", "你的預算 NT$100,000"),
        ("房貸 2.5 億", "貸款 NT$250,000,000"),
        ("I have 6k to invest", "your budget of $6,000"),
        ("月薪六萬", "月薪 NT$60,000"),
        ("月薪十二萬", "月薪 NT$120,000"),
        ("存了 3 百萬", "已存 NT$3,000,000"),
        ("預算 60000", "預算 6 萬"),
    ],
)
def test_user_number_unit_conversions(context, answer):
    result = verify_numeric_consistency(answer, [_PRICE_OUTPUT], user_context=context)
    assert result.passed, _suspicious_raw(result)


def test_numbers_from_conversation_history_are_allowed():
    """追問時回答引用前幾輪出現過的數字，不算捏造。"""
    history = "user: BTC 多少\nassistant: BTC 現價 $63,500，24h +0.8%"
    result = verify_numeric_consistency(
        "之前提到的 $63,500 跟現在 $64,000 差不多",
        [_PRICE_OUTPUT],
        user_context="現在呢？\n" + history,
    )
    assert result.passed, _suspicious_raw(result)


def test_budget_arithmetic_from_user_numbers_is_not_hallucination():
    """百分比分配、佔比、加減乘除只要能由使用者的數字推得就放行。"""
    answer = (
        "以月薪 6 萬（NT$60,000）估算：房租 NT$15,000 約佔 25%；"
        "50/30/20 法則下，必要支出 50%（NT$30,000）、想要 30%（NT$18,000）、"
        "儲蓄 20%（NT$12,000），扣掉房租後必要支出還剩 NT$15,000。"
    )
    result = verify_numeric_consistency(
        answer,
        [_PRICE_OUTPUT],
        user_context="我月薪 6 萬，房租 1.5 萬，幫我抓每月預算，BTC 現在多少",
    )
    assert result.passed, _suspicious_raw(result)


def test_rounded_ratio_from_user_numbers_is_allowed():
    """儲蓄 1 萬 / 月薪 6 萬 = 16.67%，模型寫 17% 是四捨五入不是捏造。"""
    result = verify_numeric_consistency(
        "目前儲蓄率約 17%",
        [_PRICE_OUTPUT],
        user_context="月薪 6 萬，每月存 1 萬，BTC 現在多少",
    )
    assert result.passed, _suspicious_raw(result)


def test_user_amount_times_tool_pct_is_allowed():
    """使用者的金額 × 工具給的漲跌幅（試算損益）是簡單運算。"""
    result = verify_numeric_consistency(
        "若照 +1.62% 計算，10 萬元約增加 NT$1,620",
        [_PRICE_OUTPUT],
        user_context="我有 100000 元可以買 BTC，現在多少",
    )
    assert result.passed, _suspicious_raw(result)


def test_allocation_percentages_summing_to_100_are_allowed():
    """配置建議的百分比（加起來 100）是建議不是市場數據。"""
    result = verify_numeric_consistency(
        "建議配置 BTC 60%、ETH 30%、穩定幣 10%，目前 BTC $64000",
        [_PRICE_OUTPUT],
        user_context="BTC 現在多少，怎麼配置",
    )
    assert result.passed, _suspicious_raw(result)


def test_partial_allocation_percentages_are_still_checked():
    """沒加到 100 的百分比不能當配置放行：這裡是憑空的「漲幅」。"""
    result = verify_numeric_consistency(
        "BTC 近一年漲了 85%，今年又漲 40%",
        [_PRICE_OUTPUT],
        user_context="BTC 現在多少",
    )
    assert result.has_hallucination_risk
    assert 85 in [n.value for n in result.suspicious_numbers]


# --- 真正的捏造仍然要抓到 ---------------------------------------------------


def test_fabricated_price_still_flagged_with_user_context():
    """工具說 2290、回答寫 2890：就算帶了 user_context 也要抓。"""
    result = verify_numeric_consistency(
        "台積電現價 NT$2,890，今日 +1.2%",
        ['{"symbol": "2330", "price": 2290, "change_pct": 1.2}'],
        user_context="台積電現在多少？",
    )
    assert result.has_hallucination_risk
    assert 2890 in [n.value for n in result.suspicious_numbers]


def test_fabricated_number_not_rescued_by_unrelated_user_numbers():
    """使用者的數字很多時，也不能因為湊巧湊出捏造值就放行（只接受簡單運算）。"""
    result = verify_numeric_consistency(
        "盤中曾出現 660 點的波動",
        ["price: 13.20, prev_close: 13.50"],
        user_context="SNXX 現在多少？我昨天看到 13.5，今天 9 點看過",
    )
    assert result.has_hallucination_risk
    assert 660 in [n.value for n in result.suspicious_numbers]


def test_market_tool_returned_nothing_but_answer_has_prices():
    """市場工具回空（查無資料），模型卻編出價格：照樣可疑。"""
    result = verify_numeric_consistency(
        "BTC 現價 $64,000",
        ["查無資料"],
        user_context="BTC 現在多少",
    )
    assert result.has_hallucination_risk


def test_no_market_tool_outputs_means_nothing_to_verify():
    """沒有市場資料工具的輸出（只用了 resolve_symbol／時間錨點）→ 無從驗證，放行。"""
    result = verify_numeric_consistency(
        "月薪 6 萬，儲蓄 17%，NT$7,500", [], user_context="我月薪 6 萬"
    )
    assert result.passed
    assert result.total_checked == 0


def test_extract_context_numbers_units_and_separators():
    from core.agents.verification import extract_context_numbers

    nums = extract_context_numbers(
        "BTC,ETH,2330,2317 月薪 6 萬 3k 1,500.5 1.5M 六萬 三百萬 3 百萬 6 months"
    )
    assert {2330, 2317} <= set(nums)  # 逗號分隔的代號不被黏成一個數字
    assert {6, 60000, 3, 3000, 1500.5, 1.5, 1_500_000, 3_000_000} <= set(nums)
    assert 1_000_000 not in nums  # 「3 百萬」不能再多算出一個「百萬」
    assert extract_context_numbers("") == []
    assert extract_context_numbers(None) == []  # type: ignore[arg-type]


def test_extract_context_numbers_keeps_most_recent():
    from core.agents.verification import extract_context_numbers

    text = " ".join(str(1000 + i) for i in range(100))
    nums = extract_context_numbers(text, limit=10)
    assert nums == [float(1000 + i) for i in range(90, 100)]


# ============================================================================
# 5. 只拿市場資料工具的輸出當驗證來源
# ============================================================================


def test_extract_tool_outputs_market_only_skips_utility_tools():
    from langchain_core.messages import ToolMessage

    from core.agents.verification import extract_tool_outputs_from_messages

    messages = [
        ToolMessage(
            content='{"symbol": "BTC", "market": "crypto"}',
            name="resolve_symbol",
            tool_call_id="1",
        ),
        ToolMessage(
            content="[REF: 現在 2026-10-06 14:00 UTC+8]",
            name="get_current_time_taipei",
            tool_call_id="2",
        ),
        ToolMessage(
            content="# skill body 50% 30% 20%", name="load_skill", tool_call_id="3"
        ),
        ToolMessage(content=_PRICE_OUTPUT, name="get_crypto_price", tool_call_id="4"),
        ToolMessage(
            content="vendor data 123", name="some_new_mcp_tool", tool_call_id="5"
        ),
    ]
    assert len(extract_tool_outputs_from_messages(messages)) == 5  # 預設行為不變
    market = extract_tool_outputs_from_messages(messages, market_only=True)
    assert market == [_PRICE_OUTPUT, "vendor data 123"]


def test_execute_streaming_reports_market_tool_outputs():
    """claw_loop Phase B 讀 result.data["market_tool_outputs"]：agent 必須把它帶出來。"""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "core/agents/base_react_agent.py"
    ).read_text(encoding="utf-8")
    body = src[src.index("async def execute_streaming") :]
    assert "market_only=True" in body
    assert '"market_tool_outputs": market_tool_outputs' in body
