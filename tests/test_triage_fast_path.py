"""Tests for core.agents.triage — T0 確定性快速通道的分流規則。

設計契約：
- 純規則、零 LLM、零 I/O
- 寧可漏判（走回完整 ReAct，只是慢）也不可誤判（金融問題被小模型當寒暄回掉）
- 白名單是 normalize 後的**完整比對**，不是子字串包含

因此本檔最重要的是 TestNeverFastPath：那一組只要有一個漏網，就是線上事故。
"""

from __future__ import annotations

import pytest

from core.agents.triage import (
    DEEP_ANALYSIS,
    SIMPLE_QA,
    classify_query,
    has_finance_signal,
    is_smalltalk,
)

pytestmark = pytest.mark.unit


# ============================================================================
# 必須走快速通道
# ============================================================================


class TestSmalltalkHits:
    @pytest.mark.parametrize(
        "query",
        [
            # 中文問候（繁 / 簡）
            "你好", "您好", "妳好", "哈囉", "哈啰", "嗨",
            "早安", "午安", "晚安", "早上好", "晚上好", "在嗎", "在吗",
            # 致謝 / 收尾
            "謝謝", "谢谢", "感謝", "多謝", "好的", "了解", "收到",
            "再見", "再见", "掰掰",
            # 身分 / 能力詢問（固定人設即可回答，不需工具）
            "你是誰", "你是谁", "你能做什麼", "你能做什么",
            "你會什麼", "有什麼功能", "怎麼用", "如何使用", "幫助",
            # 英文
            "hi", "hello", "hey", "good morning", "thanks", "thank you",
            "ok", "okay", "got it", "bye", "goodbye",
            "who are you", "what can you do", "help", "what is this",
            # 俄文
            "привет", "здравствуйте", "спасибо", "пока", "кто ты", "помощь",
        ],
    )
    def test_hits_fast_path(self, query):
        assert is_smalltalk(query) is True


class TestNormalization:
    @pytest.mark.parametrize(
        "query",
        [
            "你好！",
            "你好。",
            "你好！！！",
            "  你好  ",
            "你好~",
            "Hello!",
            "HELLO",          # 全大寫不可被 ticker 規則誤殺
            "Hi.",
            "OK",             # 同上：2 個大寫字母長得像代號
            "ＨＥＬＬＯ",      # 全形
            "Thanks!!",
            "  thank you  ",
        ],
    )
    def test_punctuation_case_and_width_are_normalized(self, query):
        assert is_smalltalk(query) is True


# ============================================================================
# 絕對不可走快速通道（誤判 = 線上事故）
# ============================================================================


class TestNeverFastPath:
    @pytest.mark.parametrize(
        "query",
        [
            # 影片實測中出現的真實問題
            "BTC 現在多少錢？",
            "台積電今天表現如何？",
            "現在適合進場美股嗎？",
            "黃金最近的走勢？",
            "請問今天可以買黃金嗎",
            # 開頭是寒暄、實質是分析請求 —— 完整比對必須擋下
            "你好，幫我分析 BTC",
            "嗨，台積電可以買嗎",
            "hi, what is the price of gold",
            "hello can you analyze tesla",
            # 純代號 / 數字
            "2330",
            "BTC",
            "ETH?",
            # 英文
            "should i buy tesla",
            "what is the price of gold",
            "is now a good time to invest",
            # 俄文
            "прогноз биткоина",
            "стоит ли покупать акции",
            # 錢包 / 地址（high-risk，必須進 consent gate）
            "幫我看一下這個地址",
            "我的錢包餘額",
            "check my wallet",
        ],
    )
    def test_never_hits_fast_path(self, query):
        assert is_smalltalk(query) is False

    @pytest.mark.parametrize(
        "query",
        [
            "",
            "   ",
            None,
            "!!!",
            "。。。",
        ],
    )
    def test_empty_and_punctuation_only(self, query):
        assert is_smalltalk(query) is False

    def test_long_input_never_fast_path(self):
        """超過長度上限的一律放行到完整路徑，即使開頭是寒暄。"""
        assert is_smalltalk("你好" * 20) is False
        assert is_smalltalk("hello " * 10) is False

    def test_company_names_are_not_in_whitelist(self):
        """公司名沒有列進金融語彙表（列不完），安全性靠白名單完整比對。

        這裡把該保證釘住：公司名永遠不可能通過，因為它不在 _SMALLTALK 裡。
        """
        for name in ["台積電", "Tesla", "輝達", "Nvidia", "0050"]:
            assert is_smalltalk(name) is False


# ============================================================================
# has_finance_signal —— T1 分類器的前置閘門
# ============================================================================


class TestFinanceSignalGate:
    @pytest.mark.parametrize(
        "query",
        ["BTC 多少錢", "2330", "$100", "50%", "股價", "buy crypto", "прогноз цен"],
    )
    def test_detects_signal(self, query):
        assert has_finance_signal(query) is True

    @pytest.mark.parametrize("query", ["BTC 多少", "ETH 怎麼樣", "TSLA", "BTC"])
    def test_ticker_catches_what_vocabulary_misses(self, query):
        """語彙表抓不到的問法要靠 ticker regex 擋下。

        「BTC 多少」是實際踩到的案例：語彙表有「多少錢」但沒有「多少」，
        少了 ticker 規則就會漏放進 T1，白付一次 LLM 呼叫。
        """
        assert has_finance_signal(query) is True

    def test_uppercase_smalltalk_is_gated_but_t0_catches_it_first(self):
        """全大寫寒暄會被 ticker regex 誤判——但 T0 白名單在更早就攔下了，

        所以實際走不到這個閘門，不影響結果。這裡把兩邊的分工釘住。
        """
        assert has_finance_signal("OK") is True  # 閘門會誤判
        assert is_smalltalk("OK") is True  # 但 T0 先命中，根本不會問閘門

    @pytest.mark.parametrize(
        "query", ["你好", "你可以幫我做什麼", "hi there", "привет", "", "   "]
    )
    def test_no_signal(self, query):
        assert has_finance_signal(query) is False

    def test_company_names_are_not_covered(self):
        """公司名列不完，刻意不含——所以它只能當『放行』判斷。

        釘住這個已知限制：有人日後想拿它當「安全可走快速通道」的唯一依據時，
        這個測試會告訴他不行。
        """
        assert has_finance_signal("台積電") is False
        # 但真正的快速通道判斷仍然擋得住，因為靠的是白名單完整比對
        assert is_smalltalk("台積電") is False


# ============================================================================
# T1 分類器
# ============================================================================


@pytest.mark.asyncio
class TestClassifyQuery:
    async def _invoke(self, reply):
        async def fake(_prompt):
            return reply

        return fake

    async def test_simple_qa_when_model_says_so(self):
        result = await classify_query(
            "你可以幫我做什麼", await self._invoke("simple_qa")
        )
        assert result == SIMPLE_QA

    async def test_accepts_verbose_model_reply(self):
        result = await classify_query(
            "how do I get started", await self._invoke("Category: simple_qa")
        )
        assert result == SIMPLE_QA

    async def test_finance_signal_hard_vetoes_model(self):
        """有金融訊號時，模型說 simple_qa 也不算——誤判代價太高。"""
        called = False

        async def fake(_prompt):
            nonlocal called
            called = True
            return "simple_qa"

        result = await classify_query("BTC 多少錢", fake)
        assert result == DEEP_ANALYSIS
        assert called is False, "有金融訊號時不該浪費一次 LLM 呼叫"

    async def test_ambiguous_reply_falls_back_to_deep(self):
        for reply in ["maybe", "", "both simple_qa and deep_analysis", "SIMPLE"]:
            result = await classify_query("something", await self._invoke(reply))
            assert result == DEEP_ANALYSIS, reply

    async def test_llm_failure_falls_back_to_deep(self):
        async def boom(_prompt):
            raise RuntimeError("provider 500")

        assert await classify_query("something", boom) == DEEP_ANALYSIS

    async def test_empty_query_never_calls_model(self):
        async def fake(_prompt):
            raise AssertionError("should not be called")

        assert await classify_query("", fake) == DEEP_ANALYSIS
        assert await classify_query("   ", fake) == DEEP_ANALYSIS
