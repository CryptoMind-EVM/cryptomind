"""Wrong-Scope Clarify (Phase E) 測試。

核心案例（截圖鐵證）：使用者問「台股是否值得投資」（範圍問題），中等模型
直接答「台積電（2330）…」（偷換成特定股）；或問「我想要投資」，模型被前文
context 帶偏調加密貨幣工具。should_clarify_wrong_scope 偵測這類「非空但答非所問」
的回應，回傳 clarify payload 讓前端跳釐清卡。

與既有 clarification 測試的分工：
- test_clarification.py：Phase C（查無）的 should_clarify
- 本檔：Phase E（答錯範圍）的 should_clarify_wrong_scope + 中文 ticker 偵測
"""
from __future__ import annotations

import pytest

from core.agents.clarification import (
    _extract_tickers_zh,
    _query_is_scope_question,
    should_clarify_wrong_scope,
)


class _OptionsLLM:
    """最小假 LLM：固定回 4 個選項。選項改由 LLM 產生後，觸發案例都需要它。"""

    async def ainvoke(self, messages, **kwargs):
        class _R:
            content = (
                '[{"label": "整體大盤", "hint": "整體指數"},'
                '{"label": "特定股票", "hint": "某支股票"},'
                '{"label": "某個產業", "hint": "某個板塊"},'
                '{"label": "技術面與基本面", "hint": "綜合分析"}]'
            )

        return _R()

# ============================================================================
# _extract_tickers_zh — 中文 ticker 偵測（補 _detect_query_ticker 的 gap）
# ============================================================================


def test_extract_chinese_alias():
    """中文公司名透過 alias 表 → 正確 symbol（台積電→2330）。"""
    assert "2330" in _extract_tickers_zh("台積電近期表現")


def test_extract_tw_number():
    """TW 4 位數代號（2330、2454）應被偵測。"""
    found = _extract_tickers_zh("2330 與 2454")
    assert "2330" in found
    assert "2454" in found


def test_extract_latin_ticker():
    """拉丁大寫 ticker（AAPL、NVDA）應被偵測。"""
    assert "AAPL" in _extract_tickers_zh("Apple (AAPL) is trading at $150")
    assert "NVDA" in _extract_tickers_zh("輝達 NVDA")


def test_extract_mixed_all_three():
    """三種格式同時出現：中文 + TW數字 + 拉丁。"""
    found = _extract_tickers_zh("台積電(2330)目前股價 TSMC")
    assert "2330" in found
    assert "TSMC" in found


def test_extract_no_ticker_for_broad_term():
    """純範圍詞（整體大盤）不該偵測出 ticker。"""
    assert _extract_tickers_zh("整體大盤") == set()


def test_extract_skip_known_crypto():
    """BTC/ETH 等已知 crypto 在 query 側（預設）不該被當成需要 clarify 的 ticker。"""
    assert _extract_tickers_zh("BTC and ETH") == set()


def test_extract_include_crypto_on_response_side():
    """Gap 1 修復：response 側用 include_crypto=True 保留 crypto ticker。

    query 裡的 BTC 是明確的（不需 clarify）；但 response 裡的 BTC/比特幣 是
    「模型被前文 context 帶偏，偷換標的」的證據——使用者問「我想要投資」無標的，
    模型調 crypto 工具，response 裡的 crypto 正是該攔的訊號。
    """
    assert "BTC" in _extract_tickers_zh("BTC is great", include_crypto=True)
    assert _extract_tickers_zh("BTC is great", include_crypto=True) != set()


def test_extract_empty_or_none():
    """空字串 / None → 空 set。"""
    assert _extract_tickers_zh("") == set()
    assert _extract_tickers_zh(None) == set()  # type: ignore[arg-type]


# ============================================================================
# _query_is_scope_question — 範圍問題判斷
# ============================================================================


def test_scope_question_with_market_term():
    """含市場命名空間詞 + 無 ticker → 是範圍問題。"""
    assert _query_is_scope_question("台股是否值得投資") is True
    assert _query_is_scope_question("美股適合買嗎") is True


def test_scope_question_intent_no_target():
    """無標的意圖詞（我想要投資）→ 是範圍問題。"""
    assert _query_is_scope_question("我想要投資") is True
    assert _query_is_scope_question("我想進場") is True


def test_not_scope_when_query_has_ticker():
    """query 已含具體 ticker（台積電/2330）→ 不是範圍問題。"""
    assert _query_is_scope_question("台積電值得買嗎") is False
    assert _query_is_scope_question("2330可以買嗎") is False


def test_not_scope_too_short():
    """太短/無範圍詞 → 不是範圍問題。"""
    assert _query_is_scope_question("你好") is False
    assert _query_is_scope_question("ab") is False


# ============================================================================
# should_clarify_wrong_scope — 主偵測函式（觸發案例）
# ============================================================================


@pytest.mark.asyncio
async def test_trigger_tw_stock_answered_tsmc():
    """台股問題 → 答台積電（截圖場景 1）→ 應觸發。"""
    payload = await should_clarify_wrong_scope(
            llm=_OptionsLLM(),
        query="台股是否值得投資",
        response="台積電(2330)目前股價在700元附近，技術面RSI偏高...",
    )
    assert payload is not None
    assert payload["type"] == "clarify"
    assert payload.get("select_mode") == "single"
    assert len(payload.get("options", [])) == 4
    assert "台股是否值得投資" in payload["question"]


@pytest.mark.asyncio
async def test_trigger_us_stock_answered_apple():
    """美股問題 → 答 Apple（截圖場景的英文版）→ 應觸發。"""
    payload = await should_clarify_wrong_scope(
            llm=_OptionsLLM(),
        query="美股適合買嗎",
        response="Apple (AAPL) is trading at $150 and looks bullish with strong fundamentals.",
        language="en",
    )
    assert payload is not None
    assert payload["type"] == "clarify"


@pytest.mark.asyncio
async def test_trigger_intent_no_target_answered_crypto():
    """「我想要投資」（無標的）→ 答加密貨幣（截圖場景 2）→ 應觸發。"""
    payload = await should_clarify_wrong_scope(
            llm=_OptionsLLM(),
        query="我想要投資",
        response="比特幣(BTC)目前價格67000美元，是熱門的投資選擇。",
    )
    assert payload is not None
    assert payload["type"] == "clarify"


# ============================================================================
# Gap 1 — Crypto context contamination（審計確認的真實洞）
# ============================================================================


@pytest.mark.asyncio
async def test_trigger_crypto_specific_ticker_in_response():
    """Gap 1：response 出現具體 crypto ticker（BTC/比特幣）且 query 無 crypto 意圖 → 觸發。

    修復前盲點：_extract_tickers_zh 把 BTC/ETH 等 18 個排除，response 裡的 BTC
    對它隱形 → new_tickers 空 → Phase E 不觸發。修復：response 側用
    include_crypto=True 保留 crypto ticker（query 側仍排除）。
    """
    assert (
        await should_clarify_wrong_scope(
            llm=_OptionsLLM(),
            query="我想要投資",
            response="比特幣(BTC)目前價格67000美元，是熱門選擇。",
        )
        is not None
    )


@pytest.mark.asyncio
async def test_trigger_crypto_generic_talk_no_ticker():
    """Gap 1 子情境：response 是泛 crypto 言論但無具體 ticker → 仍觸發。

    場景：使用者問「我想要投資」，模型答「加密貨幣整體來說不錯」——抓不到 BTC
    這種 ticker，但仍是偷換標的（crypto context 污染）。
    """
    payload = await should_clarify_wrong_scope(
            llm=_OptionsLLM(),
        query="我想要投資",
        response="加密貨幣整體來說是不錯的投資選擇，風險與報酬並存。",
    )
    assert payload is not None
    assert payload["type"] == "clarify"


@pytest.mark.asyncio
async def test_trigger_crypto_english_response():
    """Gap 1 英文版：response 用 Bitcoin/ethereum 等英文 crypto 詞 → 觸發。"""
    assert (
        await should_clarify_wrong_scope(
            llm=_OptionsLLM(),
            query="我想要投資",
            response="Bitcoin is a popular investment, ethereum too.",
        )
        is not None
    )


@pytest.mark.asyncio
async def test_no_trigger_when_query_genuinely_asks_crypto():
    """Gap 1 防誤觸發：query 真的問 crypto（加密貨幣值得投資嗎）→ 不觸發。

    即使 response 答 crypto 也是正確範圍，因為使用者本來就問 crypto。
    """
    assert (
        await should_clarify_wrong_scope(
            query="加密貨幣值得投資嗎",
            response="比特幣(BTC)是主流的加密投資選擇。",
        )
        is None
    )


# ============================================================================
# should_clarify_wrong_scope — 不該觸發（防誤觸發）
# ============================================================================


@pytest.mark.asyncio
async def test_no_trigger_query_has_ticker():
    """query 已指名特定股（台積電值得買嗎）→ 不是範圍問題，不觸發。"""
    assert (
        await should_clarify_wrong_scope(
            query="台積電值得買嗎",
            response="台積電近期表現不錯，技術面偏多。",
        )
        is None
    )


@pytest.mark.asyncio
async def test_no_trigger_response_has_breadth_words():
    """response 含廣度詞（整體/大盤）→ 視為正確範圍答案，不觸發。

    場景：使用者問「台股」，response 說「整體台股大盤偏多，權值股如台積電帶動」
    ——這是正確的範圍答案（台積電只是舉例），不該被攔。
    """
    assert (
        await should_clarify_wrong_scope(
            query="台股值得投資嗎",
            response="整體台股大盤偏多，權值股如台積電(2330)帶動上漲，加權指數創高。",
        )
        is None
    )


@pytest.mark.asyncio
async def test_no_trigger_empty_response():
    """空回應歸 Phase D（should_clarify_vague_query），不歸 Phase E。"""
    assert (
        await should_clarify_wrong_scope(
            query="台股值得投資嗎",
            response="",
        )
        is None
    )


@pytest.mark.asyncio
async def test_no_trigger_already_clarified():
    """已 clarify 過 → 不再觸發（防無限迴圈）。"""
    assert (
        await should_clarify_wrong_scope(
            query="台股是否值得投資",
            response="台積電(2330)目前股價...",
            already_clarified=True,
        )
        is None
    )


@pytest.mark.asyncio
async def test_no_trigger_response_no_new_ticker():
    """response 沒提 query 以外的 ticker → 不是偷換主題，不觸發。

    場景：query「台股」response「台股整體偏多」（雖然 query 是範圍問題，
    但 response 沒跳到特定 ticker，是合理的範圍答案）。
    """
    assert (
        await should_clarify_wrong_scope(
            query="台股值得投資嗎",
            response="目前看來是值得的，不過投資有風險。",
        )
        is None
    )


@pytest.mark.asyncio
async def test_no_trigger_query_not_scope_question():
    """query 既無範圍詞也無 ticker（純閒聊）→ 不觸發。"""
    assert (
        await should_clarify_wrong_scope(
            query="你好請幫我",
            response="台積電(2330)是熱門股票。",
        )
        is None
    )


# ============================================================================
# payload shape（前端零改動驗證 — 必須與 Phase D 相容）
# ============================================================================


# ============================================================================
# Model-Asked Clarify（2026-09-08 線上「我想要投資」雙重釐清案例）
#
# 模型沒照 shared.yaml 指示呼叫 clarify 工具，改把釐清問題寫成純文字串流
# （「想先確認你打算投資哪個方向…你想從哪個開始？」＋列舉各市場例子）。
# 修復前 Phase E 把列舉例子裡的 ticker（BTC/台積電 2330…）誤讀成「偷換標的」
# 證據，再彈第二張釐清卡 → 使用者被問兩次、兩組選項還不一致。
# 修復：response 本身是釐清問句（問號/問句引導詞 + 無實質市場數據）→ 不觸發。
# ============================================================================


# 線上真實案例原文（2026-09-08 使用者回報，模型串流出的純文字釐清）
_INCIDENT_RESPONSE_ZH = (
    "好的！為了給你真正有用的建議，想先確認你打算投資哪個方向，我才能查即時數據分析：\n"
    "加密貨幣（如 BTC、ETH、SOL，或你想看的其他幣）\n"
    "美股（如 Apple、NVIDIA、TSLA 等）\n"
    "台股（如台積電 2330 等個股）\n"
    "其他市場（港股 / 日股 / 大宗商品 / 外匯等）\n"
    "另外，如果你能順便說一下投資目標（短線波段 / 長期持有）和風險承受度"
    "（保守 / 積極），我可以把建議做得更貼近你的需求。\n"
    "你想從哪個開始？"
)


def test_response_is_clarifying_question_incident():
    """偵測器單元：線上案例原文（列舉 ticker 當例子 + 問句結尾）→ 是釐清問句。"""
    from core.agents.clarification import _response_is_clarifying_question

    assert _response_is_clarifying_question(_INCIDENT_RESPONSE_ZH) is True


def test_response_is_clarifying_question_english():
    """偵測器單元：英文版純文字釐清（which would you like + ?）→ True。"""
    from core.agents.clarification import _response_is_clarifying_question

    assert (
        _response_is_clarifying_question(
            "Sure! To give you useful advice I'd like to confirm which direction "
            "you want to invest in: Cryptocurrency (BTC, ETH), US stocks "
            "(Apple, NVIDIA), or Taiwan stocks. Which would you like to start with?"
        )
        is True
    )


def test_response_is_clarifying_question_rejects_data_answers():
    """偵測器單元：含實質數據的答案（股價/百分比/技術指標）→ 不是純發問。"""
    from core.agents.clarification import _response_is_clarifying_question

    assert _response_is_clarifying_question("台積電(2330)目前股價在700元附近，RSI偏高") is False
    assert _response_is_clarifying_question("Apple (AAPL) is trading at $150") is False
    assert _response_is_clarifying_question("本周上漲 2.3%，技術面偏多") is False


def test_response_is_clarifying_question_rejects_plain_statements():
    """偵測器單元：無問句訊號的泛論述（Gap 1 既有案例）→ 不是釐清問句。"""
    from core.agents.clarification import _response_is_clarifying_question

    assert (
        _response_is_clarifying_question("加密貨幣整體來說是不錯的投資選擇，風險與報酬並存。")
        is False
    )
    assert _response_is_clarifying_question("Bitcoin is a popular investment, ethereum too.") is False


def test_response_is_clarifying_question_empty_or_none():
    """偵測器單元：空字串 / None → False（防呆）。"""
    from core.agents.clarification import _response_is_clarifying_question

    assert _response_is_clarifying_question("") is False
    assert _response_is_clarifying_question(None) is False


@pytest.mark.asyncio
async def test_no_trigger_when_response_is_clarifying_question():
    """線上真實案例（zh）：模型把釐清寫成純文字 → Phase E 不可再彈第二張卡。

    修復前：response 裡列舉的 BTC/ETH/SOL/2330 被當成「query 沒提的新 ticker」
    → 誤判答錯範圍 → double clarify（問兩次、選項不一致）。
    """
    assert (
        await should_clarify_wrong_scope(
            query="我想要投資",
            response=_INCIDENT_RESPONSE_ZH,
        )
        is None
    )


@pytest.mark.asyncio
async def test_no_trigger_when_response_is_clarifying_question_en():
    """英文版：模型純文字釐清（Which would you like?）→ 不觸發。"""
    assert (
        await should_clarify_wrong_scope(
            query="美股適合買嗎",
            response=(
                "Good question! Could you clarify what you mean by US stocks: "
                "the overall market index, or a specific stock like Apple or "
                "NVIDIA? Which would you like to start with?"
            ),
            language="en",
        )
        is None
    )


@pytest.mark.asyncio
async def test_still_trigger_when_response_asks_but_has_data():
    """保守邊界：問句裡含實質數據（幣價/RSI）→ 是「半答半問的答錯範圍」，
    仍要觸發釐清（數據優先於問句訊號，避免假問句繞過攔截）。"""
    payload = await should_clarify_wrong_scope(
            llm=_OptionsLLM(),
        query="我想要投資",
        response="你想投資比特幣(BTC)嗎？目前價格67000美元，技術面RSI 65偏多。",
    )
    assert payload is not None
    assert payload["type"] == "clarify"


# ============================================================================
# payload shape（前端零改動驗證 — 必須與 Phase D 相容）
# ============================================================================


@pytest.mark.asyncio
async def test_payload_shape_matches_phase_d():
    """Phase E payload shape 必須與 Phase D（should_clarify_vague_query）一致，
    讓前端 chat-hitl.js 既有 inline clarify 氣泡直接渲染（PR #316）。"""
    payload = await should_clarify_wrong_scope(
            llm=_OptionsLLM(),
        query="台股值得投資嗎",
        response="台積電(2330)股價700元，技術面偏多。",
    )
    assert payload is not None
    # 必要欄位
    assert "type" in payload and payload["type"] == "clarify"
    assert "question" in payload and isinstance(payload["question"], str)
    # options 每項是 {label, hint}（支援前端可點選按鈕）
    options = payload.get("options", [])
    assert len(options) == 4
    for opt in options:
        assert "label" in opt
        assert "hint" in opt
    assert payload.get("select_mode") == "single"


# ============================================================================
# 2026-10-06：個人理財問題＋完整回答被誤彈釐清卡（「月薪六萬，又想還款又想投資」）
# ============================================================================


class _CountingLLM(_OptionsLLM):
    """記錄被呼叫次數：規則層就該擋掉的案例，連 LLM 都不該叫（省一次呼叫）。"""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages, **kwargs):
        self.calls += 1
        return await super().ainvoke(messages, **kwargs)


_BUDGET_QUERY = "我在想該怎麼規劃還他如果我一個月薪水六萬（淨潤），但又想投資"
_BUDGET_RESPONSE = """月薪 6 萬可以先抓 50/30/20：必要開銷 50%（3 萬元）、還款與儲蓄 30%、投資 20%（1.2 萬元）。
四、我的建議步驟
1 先確認欠款是否有息、時間壓力（影響優先級）。
4 投資端：從低風險藍籌或高股息 ETF 開始，月繳制（定時定額）降低擇時風險。
想讓我幫你做得更具體嗎？可以告訴我：
你想投資的標的或市場（台股／美股／加密）？"""


@pytest.mark.asyncio
async def test_budget_question_with_full_answer_does_not_pop_card():
    """使用者截圖案例：回答含百分比與金額（讓「回答是反問句」的防護失效）＋泛稱 ETF，
    以前會被當成偷換標的而彈出「整體大盤／特定股票」卡。現在規則層就擋掉，連 LLM 都不叫。"""
    llm = _CountingLLM()
    payload = await should_clarify_wrong_scope(
        query=_BUDGET_QUERY, response=_BUDGET_RESPONSE, llm=llm
    )
    assert payload is None
    assert llm.calls == 0


def test_generic_acronyms_are_not_tickers():
    from core.agents.clarification import _extract_tickers_zh

    assert _extract_tickers_zh("高股息 ETF、REIT 與 ETN，注意 APR 與 CPI") == set()
    assert "AAPL" in _extract_tickers_zh("Apple (AAPL) 與 ETF")


def test_amounts_and_years_are_not_tw_tickers():
    from core.agents.clarification import _extract_tickers_zh

    assert _extract_tickers_zh("月薪 60000 元，預算 30000 元，2026 年再檢視") == set()
    assert "2330" in _extract_tickers_zh("台積電(2330)目前股價")


@pytest.mark.asyncio
async def test_non_investment_scope_phrase_skips_phase_e():
    """「要不要」「會不會」這類詞在非投資問題也很常見（露營、換工作）；
    Phase E 只該處理投資範圍問題，其他的連 LLM 都不叫。"""
    llm = _CountingLLM()
    payload = await should_clarify_wrong_scope(
        query="週末要不要去露營",
        response="BTC 現在 $67,000，RSI 55，適合露營時順手看盤。",
        llm=llm,
    )
    assert payload is None
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_phase_e_question_does_not_claim_confusion_after_answering():
    """模型已經回答完了，卡片不能再寫「我不太確定你問的…」自相矛盾。"""
    payload = await should_clarify_wrong_scope(
        query="台股是否值得投資",
        response="台積電(2330)目前股價在700元附近，技術面RSI偏高...",
        llm=_OptionsLLM(),
    )
    assert payload is not None
    assert "不太確定" not in payload["question"]
    assert "台股是否值得投資" in payload["question"]
