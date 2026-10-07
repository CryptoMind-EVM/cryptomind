"""
ReAct recovery helpers 的單元測試。

覆蓋 ``core/agents/recovery.py`` 的三個核心 helper：
- ``looks_like_ack_without_tool``：ack continuation 偵測
- ``is_thinking_only`` / ``extract_reasoning_content``：reasoning model 偵測
- ``RetryCounter``：retry budget tracking
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.agents.recovery import (
    RETRY_BUDGET,
    RetryCounter,
    extract_reasoning_content,
    is_thinking_only,
    looks_like_ack_without_tool,
)

# ============================================================================
# 1. looks_like_ack_without_tool
# ============================================================================


def _ack_cases():
    """(reply, used_tools, expected, description)"""
    return [
        # --- 正向：應該觸發 ---
        ("好的，我來查比特幣現價", [], True, "zh-TW ack"),
        ("我來幫你查一下", [], True, "zh-TW 我來幫你查"),
        ("让我看看", [], True, "zh-CN 让我"),
        ("马上查", [], True, "zh-CN 马上查"),
        ("稍等一下", [], True, "zh-TW 稍等"),
        ("Let me check that for you", [], True, "en let me check"),
        ("I'll look it up", [], True, "en I'll look"),
        ("Just a moment", [], True, "en just a moment"),
        ("Checking now", [], True, "en checking"),
        ("сейчас проверю", [], True, "ru сейчас проверю"),
        ("минуту", [], True, "ru минуту"),
        # --- 負向：不該觸發 ---
        # 用了工具就不該觸發（即使回 ack）
        ("好的我來查", ["get_crypto_price"], False, "已用工具"),
        # 太長（正經回答）
        ("BTC 現價 $64,000，建議分批佈局..." + "x" * 100, [], False, "長回應"),
        # 空
        ("", [], False, "空回應"),
        (None, [], False, "None"),
        # 一般短回應（非 ack）
        ("BTC 現價 $64,000", [], False, "正常短回應"),
        ("你好", [], False, "問候"),
        ("我不知道 SNXX 是什麼", [], False, "正常承認不知道"),
        # 含 ack 詞但不是 ack pattern
        ("Please use the resolve_symbol tool first", [], False, "instruction"),
        # --- M3：完整的短答案不是「說要查但沒查」---
        # 已經算完、帶數字結論
        (
            "我幫你算過了：月付約 2.3 萬，總利息約 85 萬。",
            [],
            False,
            "已算完＋數字結論",
        ),
        (
            "我幫你算過了，月付約 2.3 萬，總利息約 85 萬。",
            [],
            False,
            "已算完（無冒號）",
        ),
        ("Let me summarize — you save 17% of your income.", [], False, "en 無冒號帶 %"),
        ("為您整理：50/30/20 法則…", [], False, "為您整理＋冒號後有內容"),
        ("我來說明：複利就是利滾利，時間越長效果越明顯", [], False, "我來說明＋內容"),
        ("為您整理如下\n- 房租 30%\n- 儲蓄 20%", [], False, "條列內容"),
        (
            "Let me summarize: you save 17% of your income.",
            [],
            False,
            "en 結論帶百分比",
        ),
        (
            "Let me calculate: the payment is about $1,250 a month.",
            [],
            False,
            "en 帶金額",
        ),
        # 建議句、問候後的陳述句含「查一下／看一下／сейчас」不是 ack
        ("建議您先查一下自己的現金流。", [], False, "建議句含查一下"),
        ("你可以先看一下每月固定支出佔比。", [], False, "建議句含看一下"),
        ("Сейчас рынок в состоянии неопределённости.", [], False, "ru 陳述句含 сейчас"),
        ("Сейчас BTC торгуется около $64,000.", [], False, "ru 現價陳述"),
        ("Sure! Let me know if you need anything else.", [], False, "en let me know"),
        # --- 真正的 ack（句號結尾、帶股票代號數字）仍要抓 ---
        ("好的，我來查比特幣現價。", [], True, "句號結尾的 ack"),
        ("我來查一下 2330 的走勢", [], True, "ack 含股票代號數字"),
        ("我來看看台積電近 3 個月的走勢", [], True, "ack 含期間數字"),
        ("好的，我來幫你查一下台積電現在的價格", [], True, "我來幫你查一下"),
        ("讓我查一下", [], True, "zh-TW 讓我查一下"),
        ("稍等，我幫你算一下", [], True, "稍等＋我幫你算"),
        ("我來查：", [], True, "冒號後沒有內容"),
        ("我來查：BTC 與 ETH 的現價和近期走勢", [], True, "冒號後只是查詢主題"),
        ("Let me check the current BTC price for you.", [], True, "en 句號結尾 ack"),
        ("Сейчас проверю курс.", [], True, "ru сейчас проверю 句號"),
        ("Одну секунду", [], True, "ru 一秒"),
    ]


@pytest.mark.parametrize(
    "reply,used_tools,expected,desc",
    _ack_cases(),
    ids=[c[3] for c in _ack_cases()],
)
def test_looks_like_ack_without_tool(reply, used_tools, expected, desc):
    actual = looks_like_ack_without_tool(reply, used_tools)
    assert actual is expected, f"{desc}: expected {expected}, got {actual}"


def test_looks_like_ack_max_length_boundary():
    """長度 = 80 不觸發，長度 = 79 觸發（邊界）。"""
    # ack 詞 4 字 + 75 字 padding = 79
    short = "我來" + "x" * 77  # len=79
    long = "我來" + "x" * 78  # len=80
    assert looks_like_ack_without_tool(short, []) is True
    assert looks_like_ack_without_tool(long, []) is False


def test_looks_like_ack_accepts_language_param_without_using_it():
    """language 參數目前不影響判斷（保留介面）。"""
    for lang in ["zh-TW", "zh-CN", "en", "ru", None]:
        assert looks_like_ack_without_tool("我來查", [], lang) is True


@pytest.mark.parametrize("lang", ["zh-TW", "zh-CN", "en", "ru"])
def test_ack_nudge_is_domain_neutral(lang):
    """M3：nudge 不能寫死加密工具名——股票／個人理財題被逼著去呼叫加密工具。

    也要給模型一條出口：不需要工具的題目（純計算、觀念說明）直接回答。
    """
    from core.i18n import reload_for_tests, t

    reload_for_tests()
    text = t("llm_sections.nudge.ack_without_tool", lang)
    assert text != "llm_sections.nudge.ack_without_tool"  # key 存在
    for tool_name in ("get_crypto_price", "resolve_symbol", "web_search"):
        assert tool_name not in text
    assert text.strip()


# ============================================================================
# 2. extract_reasoning_content + is_thinking_only
# ============================================================================


def _mock_msg(
    content=None,
    reasoning_content=None,
    response_metadata=None,
    additional_kwargs=None,
):
    """建一個 mock AIMessage-like 物件。

    additional_kwargs 可直接傳入完整 dict（覆蓋 reasoning_content 的便利參數），
    用於測試 reasoning 放在非標準 key 的 provider（如 DeepSeek-R1 的
    additional_kwargs.reasoning）。
    """
    msg = MagicMock()
    msg.content = content if content is not None else ""
    if additional_kwargs is not None:
        msg.additional_kwargs = additional_kwargs
    else:
        ak = {}
        if reasoning_content is not None:
            ak["reasoning_content"] = reasoning_content
        msg.additional_kwargs = ak
    msg.response_metadata = response_metadata or {}
    msg.tool_calls = None
    return msg


def test_extract_reasoning_content_from_additional_kwargs():
    """NVIDIA nemotron / OpenAI o-series 把 reasoning 放在 additional_kwargs。"""
    msg = _mock_msg(content="", reasoning_content="思考中... SNXX 可能是某代幣")
    rc = extract_reasoning_content(msg)
    assert "SNXX" in rc


def test_extract_reasoning_content_from_response_metadata():
    """部分 provider 放在 response_metadata。"""
    msg = _mock_msg(
        content="",
        response_metadata={"reasoning_content": "maybe it's a token"},
    )
    rc = extract_reasoning_content(msg)
    assert "maybe" in rc


def test_extract_reasoning_content_from_anthropic_thinking_block():
    """Anthropic thinking 放在 content list 的 thinking type。"""
    msg = MagicMock()
    msg.content = [
        {"type": "thinking", "thinking": "I should consider..."},
        {"type": "text", "text": ""},  # text 空
    ]
    msg.additional_kwargs = {}
    msg.response_metadata = {}
    rc = extract_reasoning_content(msg)
    assert "consider" in rc


def test_extract_reasoning_content_empty_when_no_reasoning():
    msg = _mock_msg(content="Hello", reasoning_content=None)
    assert extract_reasoning_content(msg) == ""


def test_extract_reasoning_content_from_additional_kwargs_reasoning_key():
    """DeepSeek-R1 系 / 部分 Qwen3 透過 OpenAI 相容端點把 thinking 放在
    additional_kwargs.reasoning（而非 reasoning_content）。

    Regression：線上 bug — reasoning model 思考完 content 空，但 thinking 在
    additional_kwargs.reasoning，extract_reasoning_content 漏抓 → is_thinking_only
    回 False → 走錯 recovery 分支（truly empty 而非 thinking-only）→ 用戶看到
    「模型沒有產生回應」。此測試證明該 key 要被抓到。
    """
    msg = _mock_msg(
        content="",
        additional_kwargs={"reasoning": "思考：美股 VIX 偏高，需查工具確認支撐位..."},
    )
    rc = extract_reasoning_content(msg)
    assert "VIX" in rc


def test_extract_reasoning_content_from_additional_kwargs_thinking_key():
    """部分 OpenAI 相容 reasoning 模型用 additional_kwargs.thinking。"""
    msg = _mock_msg(
        content="",
        additional_kwargs={"thinking": "我需要先查即時數據..."},
    )
    rc = extract_reasoning_content(msg)
    assert "即時數據" in rc


def test_extract_reasoning_content_from_response_metadata_thinking_key():
    """response_metadata.thinking 也是某些 provider 用的位置。"""
    msg = _mock_msg(
        content="",
        response_metadata={"thinking": "分析市場狀況中..."},
    )
    rc = extract_reasoning_content(msg)
    assert "市場" in rc


def test_is_thinking_only_true_when_reasoning_in_reasoning_key():
    """核心 regression：DeepSeek-R1 思考完 content 空，reasoning 在
    additional_kwargs.reasoning → 應判定為 thinking-only（觸發 prefill nudge），
    而非 truly empty。"""
    msg = _mock_msg(
        content="",
        additional_kwargs={"reasoning": "使用者問預測，我該查工具再給判斷..."},
    )
    assert is_thinking_only(msg) is True


def test_extract_reasoning_content_handles_none_msg():
    assert extract_reasoning_content(None) == ""


def test_is_thinking_only_true_when_content_empty_but_reasoning_present():
    """核心 case：nemotron 思考完但 content 空。"""
    msg = _mock_msg(content="", reasoning_content="分析中...")
    assert is_thinking_only(msg) is True


def test_is_thinking_only_false_when_content_present():
    """有 content 就不是 thinking-only（即使有 reasoning）。"""
    msg = _mock_msg(content="BTC $64,000", reasoning_content="思考中")
    assert is_thinking_only(msg) is False


def test_is_thinking_only_false_when_both_empty():
    """都空也不是 thinking-only（是 truly empty）。"""
    msg = _mock_msg(content="", reasoning_content=None)
    assert is_thinking_only(msg) is False


def test_is_thinking_only_false_for_none_msg():
    assert is_thinking_only(None) is False


def test_is_thinking_only_handles_anthropic_thinking_block():
    msg = MagicMock()
    msg.content = [{"type": "thinking", "thinking": "thoughts"}]
    # text 部分 missing → visible 空
    msg.additional_kwargs = {}
    msg.response_metadata = {}
    msg.tool_calls = None
    assert is_thinking_only(msg) is True


# ============================================================================
# 3. RetryCounter
# ============================================================================


def test_retry_counter_initial_budget():
    counter = RetryCounter()
    for kind, expected in RETRY_BUDGET.items():
        assert counter.remaining(kind) == expected
        assert counter.can_retry(kind) is True


def test_retry_counter_consume():
    counter = RetryCounter()
    assert counter.can_retry("ack_without_tool") is True
    counter.consume("ack_without_tool")
    assert counter.can_retry("ack_without_tool") is False
    assert counter.remaining("ack_without_tool") == 0


def test_retry_counter_consume_does_not_go_negative():
    counter = RetryCounter()
    counter.consume("ack_without_tool")
    counter.consume("ack_without_tool")  # 多消耗一次
    assert counter.remaining("ack_without_tool") == 0


def test_retry_counter_total_remaining():
    counter = RetryCounter()
    initial = counter.total_remaining()
    assert initial == sum(RETRY_BUDGET.values())
    counter.consume("ack_without_tool")
    assert counter.total_remaining() == initial - 1


def test_retry_counter_unknown_kind():
    counter = RetryCounter()
    assert counter.can_retry("nonexistent") is False
    assert counter.remaining("nonexistent") == 0


def test_retry_counter_isolates_kinds():
    """消耗 ack 不影響 thinking_only 額度。"""
    counter = RetryCounter()
    counter.consume("ack_without_tool")
    assert counter.can_retry("thinking_only") is True
    assert counter.can_retry("truly_empty") is True


# ============================================================================
# 4. RetryCounter — total budget + grace call
# ============================================================================


def test_retry_counter_total_budget_caps_all_retries():
    """即使各類別還有額度，總量上限到了就不能再 retry。

    預設 TOTAL_GRACE_BUDGET=4，類別各 1（共 3）。
    若把類別額度改成 2 各，總量 4 會比類別總和 6 小，
    消耗 4 次後就不能再 retry（即使類別還有）。
    """
    from core.agents.recovery import TOTAL_GRACE_BUDGET

    # 用較寬鬆的類別額度 + 預設總量，驗證總量上限生效
    counter = RetryCounter(total_budget=2)
    counter.consume("ack_without_tool")  # total=1
    counter.consume("thinking_only")  # total=2 → 上限到
    # truly_empty 類別額度還有，但總量到了
    assert counter.remaining("truly_empty") == 1
    assert counter.can_retry("truly_empty") is False
    assert counter.total_consumed() == 2
    assert TOTAL_GRACE_BUDGET >= 3  # 預設至少能跑完 3 種 recovery


def test_retry_counter_total_budget_lower_than_sum_caps_early():
    """若 total_budget 設得比各類別總和小，會提早卡住。"""
    counter = RetryCounter(total_budget=2)
    counter.consume("ack_without_tool")
    counter.consume("thinking_only")
    # 總量 2 已到，即使 truly_empty 類別額度還有 1 也不能用
    assert counter.remaining("truly_empty") == 1
    assert counter.can_retry("truly_empty") is False


def test_retry_counter_grace_call_detection():
    """is_grace_call 在最後一格時為 True。"""
    counter = RetryCounter(total_budget=3)
    assert counter.is_grace_call() is False  # 0/3
    counter.consume("ack_without_tool")  # 1/3
    assert counter.is_grace_call() is False
    counter.consume("thinking_only")  # 2/3
    assert counter.is_grace_call() is True  # 最後一格
    counter.consume("truly_empty")  # 3/3
    assert counter.is_grace_call() is False  # 已超過
