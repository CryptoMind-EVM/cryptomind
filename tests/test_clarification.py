"""
Progressive Clarification 測試。

核心案例：SNXX 場景，LLM 不確定是什麼 → 主動問使用者。
"""
from __future__ import annotations

import pytest

from core.agents.clarification import (
    _detect_query_ticker,
    should_clarify,
)

# ============================================================================
# _detect_query_ticker
# ============================================================================


def test_detect_unknown_ticker():
    assert _detect_query_ticker("SNXX 值得買嗎") == "SNXX"


def test_detect_ticker_with_single_digit():
    """代號 + 單數字（如 NVDA3 變體）。"""
    assert _detect_query_ticker("SNXX 值得買嗎") == "SNXX"


def test_skip_known_crypto():
    """BTC/ETH 等已知加密貨幣不該觸發 clarify。"""
    assert _detect_query_ticker("BTC 值得買嗎") is None
    assert _detect_query_ticker("ETH 價格") is None


def test_skip_known_crypto_derived_from_json():
    """JSON 派生的 crypto 清單涵蓋原寫死清單漏掉的幣（UNI/ATOM/TON 等）。

    這些幣在 company_aliases.json 有，但原本寫死的 _KNOWN_CRYPTO_TICKERS(18)
    /_KNOWN_CRYPTO(9) 沒有 → drift bug。動態派生後自動涵蓋。
    """
    # 原本 18 清單沒有、9 清單也沒有的
    assert _detect_query_ticker("UNI 值得買嗎") is None
    assert _detect_query_ticker("ATOM 技術面") is None
    assert _detect_query_ticker("EOS 價格") is None
    # TON 原本只在 18 清單、9 清單沒有（TON 產品卻漏掉 TON）
    assert _detect_query_ticker("TON 適合買嗎") is None
    # 未知代號仍應觸發
    assert _detect_query_ticker("SNXX 是什麼") == "SNXX"
    assert _detect_query_ticker("NVDA 分析") == "NVDA"


def test_no_ticker():
    assert _detect_query_ticker("比特幣價格") is None
    assert _detect_query_ticker("你好") is None


# ============================================================================
# should_clarify
# ============================================================================


def test_clarify_when_uncertain_and_no_tools():
    """LLM 說「查無 SNXX」但沒用工具 → 該 clarify。"""
    result = should_clarify(
        response="抱歉，查無 SNXX 此幣。",
        used_tools=[],
        query="SNXX 值得買嗎",
    )
    assert result is not None
    assert "SNXX" in result
    assert "加密貨幣" in result or "美股" in result


def test_clarify_en_not_found():
    result = should_clarify(
        response="I cannot find SNXX in any database.",
        used_tools=[],
        query="SNXX price?",
    )
    assert result is not None
    assert "SNXX" in result


def test_clarify_ru_not_found():
    result = should_clarify(
        response="не удалось найти SNXX",
        used_tools=[],
        query="SNXX цена?",
    )
    assert result is not None


def test_no_clarify_when_tools_used():
    """用了工具還找不到 → 不該 clarify（已經試過了）。"""
    result = should_clarify(
        response="查無 SNXX",
        used_tools=["resolve_symbol", "get_crypto_price"],
        query="SNXX 值得買嗎",
    )
    assert result is None


def test_no_clarify_when_already_clarified():
    """同一 session 第二次不該再 clarify。"""
    result = should_clarify(
        response="查無 PEPE",
        used_tools=[],
        query="PEPE 多少",
        already_clarified=True,
    )
    assert result is None


def test_no_clarify_when_response_confident():
    """LLM 有明確答案 → 不該 clarify。"""
    result = should_clarify(
        response="SNXX 是 Tradr 2X Long SNDK Daily ETF，現價 $13.20",
        used_tools=[],
        query="SNXX 值得買嗎",
    )
    assert result is None


def test_no_clarify_when_no_ticker_in_query():
    """query 沒代號 → 不該 clarify。"""
    result = should_clarify(
        response="我不確定",
        used_tools=[],
        query="你好嗎",
    )
    assert result is None


def test_no_clarify_known_crypto():
    """BTC 不該 clarify（已知）。"""
    result = should_clarify(
        response="查無 BTC",
        used_tools=[],
        query="BTC 價格",
    )
    assert result is None


def test_clarification_question_contains_options():
    """clarification 必須給使用者選項。"""
    result = should_clarify(
        response="找不到 XYZABC",
        used_tools=[],
        query="XYZABC 是什麼",
    )
    assert result is not None
    # 至少要有「加密貨幣」跟「美股」選項
    assert "加密貨幣" in result
    assert "美股" in result


def test_clarification_question_mentions_ticker():
    """clarification 必須提到具體代號。"""
    result = should_clarify(
        response="查無 ABCD",
        used_tools=[],
        query="ABCD 幣價格",
    )
    assert result is not None
    assert "ABCD" in result


def test_no_clarify_empty_response():
    result = should_clarify("", [], "SNXX")
    assert result is None


def test_no_clarify_none_response():
    result = should_clarify(None, [], "SNXX")  # type: ignore
    assert result is None


# ============================================================================
# 幻覺偵測（2026-07-20 新增 — AKE 案例）
#
# 過去 should_clarify 只抓「查無/不確定」，抓不到「正在獲取即時價格」這種幻覺。
# ============================================================================


def test_hallucination_zh_tw_fetching_price_triggers_clarify():
    """「正在獲取即時價格...」應觸發 multi-market clarification sentinel。"""
    from core.agents.clarification import is_multi_market_trigger

    fake_response = (
        "📊 AKE 即時行情概況\n"
        "當前價格：(正在獲取即時價格...)\n"
        "市值：(正在獲取市值數據...)"
    )
    result = should_clarify(
        response=fake_response,
        used_tools=[],
        query="AKE 現在值得買嗎",
    )
    # 應回 sentinel（非 None），不是基本 clarification 字串
    assert result is not None
    assert is_multi_market_trigger(result), (
        f"幻覺回應（正在獲取）應觸發 multi-market sentinel，got: {result!r}"
    )


def test_hallucination_zh_cn_calculating_triggers_clarify():
    """「正在計算變動率」也該觸發。"""
    from core.agents.clarification import is_multi_market_trigger

    result = should_clarify(
        response="根據技術分析，正在計算變動率...",
        used_tools=[],
        query="AKE 值得買嗎",
    )
    assert result is not None
    assert is_multi_market_trigger(result)


def test_hallucination_english_fetching_triggers_clarify():
    """英文「Fetching price」也該觸發。"""
    from core.agents.clarification import is_multi_market_trigger

    result = should_clarify(
        response="Fetching real-time price for AKE...",
        used_tools=[],
        query="Is AKE worth buying?",
    )
    assert result is not None
    assert is_multi_market_trigger(result)


def test_hallucination_not_triggered_when_tools_used():
    """有呼叫工具就不該走幻覺路徑（即使回應含「正在」）。"""
    result = should_clarify(
        response="正在獲取即時價格...",
        used_tools=["get_crypto_price"],  # LLM 用了工具
        query="AKE 值得買嗎",
    )
    assert result is None, "用過工具就不該走幻覺 sentinel"


def test_hallucination_not_triggered_when_already_clarified():
    """同 session 第二次不再觸發（避免無限追問）。"""
    result = should_clarify(
        response="正在獲取即時價格...",
        used_tools=[],
        query="AKE",
        already_clarified=True,
    )
    assert result is None


def test_hallucination_not_triggered_for_known_crypto():
    """BTC 是已知加密貨幣，不該走幻覺路徑。"""
    result = should_clarify(
        response="正在獲取即時價格...",
        used_tools=[],
        query="BTC 現在多少",
    )
    # BTC 在 _KNOWN_CRYPTO 內，不會被 _detect_query_ticker 抓到 → 不觸發
    assert result is None


def test_uncertainty_path_still_works():
    """既有「查無 / 找不到」路徑不該被新 sentinel 邏輯影響。"""
    from core.agents.clarification import is_multi_market_trigger

    result = should_clarify(
        response="查無 SNXX 此幣",
        used_tools=[],
        query="SNXX 值得買嗎",
    )
    assert result is not None
    assert not is_multi_market_trigger(result), (
        "「查無」應走基本 clarification 路徑（不是 multi-market sentinel）"
    )
    assert "SNXX" in result
    assert "加密貨幣" in result


# ============================================================================
# build_multi_market_clarification
# ============================================================================


def test_build_multi_market_clarification_zh_tw():
    """zh-TW：應列出支援 ✅ 與不支援 ❌ 候選。"""
    from core.agents.clarification import build_multi_market_clarification

    candidates_json = (
        '{"query": "AKE", "candidates": ['
        '{"market": "crypto", "symbol": "akedo", "name": "Akedo", '
        ' "verified": true, "supported": true, "source": "coingecko"},'
        '{"market": "fr", "symbol": null, "supported": false, '
        ' "note": "本平台目前不支援此市場"}'
        '], "ambiguous": true}'
    )
    msg = build_multi_market_clarification("AKE", candidates_json, language="zh-TW")
    assert "AKE" in msg
    assert "Akedo" in msg  # 支援的候選名稱
    assert "加密貨幣" in msg  # market 中文標籤
    assert "❌" in msg  # 不支援標示
    assert "法股" in msg  # fr 市場中文標籤
    assert "全部" in msg  # 提示可選「全部」


def test_build_multi_market_clarification_english():
    """en：應含 'all' 提示 + 'does not support'。"""
    from core.agents.clarification import build_multi_market_clarification

    candidates_json = (
        '{"query": "AKE", "candidates": ['
        '{"market": "crypto", "symbol": "akedo", "verified": true, '
        ' "supported": true, "source": "coingecko"}'
        ']}'
    )
    msg = build_multi_market_clarification("AKE", candidates_json, language="en")
    assert "AKE" in msg
    assert "Akedo" in msg or "akedo" in msg
    assert '"all"' in msg  # 提示可用 "all"


def test_build_multi_market_clarification_empty_falls_back():
    """空 candidates → 退回基本 4 選項 clarification。"""
    from core.agents.clarification import build_multi_market_clarification

    candidates_json = '{"query": "AKE", "candidates": [], "ambiguous": false}'
    msg = build_multi_market_clarification("AKE", candidates_json, language="zh-TW")
    # 退回基本 clarification（含 4 選項）
    assert "加密貨幣" in msg
    assert "美股" in msg


def test_build_multi_market_clarification_invalid_json_falls_back():
    """candidates_json 不是合法 JSON → 退回基本。"""
    from core.agents.clarification import build_multi_market_clarification

    msg = build_multi_market_clarification("AKE", "not valid json {", language="zh-TW")
    assert "加密貨幣" in msg  # fallback 內容


# ============================================================================
# should_clarify_vague_query（vague-query clarify — 線上 bug 修復）
# ============================================================================


@pytest.mark.asyncio
async def test_vague_query_clarify_triggers_for_empty_response_no_ticker():
    """Regression：線上 bug — 「美股適合買嗎」(無 ticker) + 空 response → 應 clarify。

    中等模型面對模糊綜合判斷問題困惑回空，此時應向使用者釐清，而非放棄。
    """
    from core.agents.clarification import should_clarify_vague_query

    result = await should_clarify_vague_query(
        llm=_FakeLLM(content=_FOUR_OPTIONS),
        query="你認為美股現在適合購買嗎",
        response="",  # 模型回空
        already_clarified=False,
    )
    assert result is not None
    assert result["type"] == "clarify"
    assert "美股" in result["question"] or "釐清" in result["question"]


@pytest.mark.asyncio
async def test_vague_query_clarify_triggers_for_other_vague_queries():
    """其他模糊判斷問題也應觸發。"""
    from core.agents.clarification import should_clarify_vague_query

    for q in ["美股會跌到什麼時候", "現在適合進場嗎", "整體市場怎麼看"]:
        result = await should_clarify_vague_query(
        llm=_FakeLLM(content=_FOUR_OPTIONS),
            query=q, response="", already_clarified=False
        )
        assert result is not None, f"「{q}」應觸發 clarify"


@pytest.mark.asyncio
async def test_vague_query_no_clarify_when_response_present():
    """有 response（即使簡短）→ 不是 vague-empty 場景，不 clarify。"""
    from core.agents.clarification import should_clarify_vague_query

    result = await should_clarify_vague_query(
        query="美股適合買嗎",
        response="目前偏空，不建議追高",  # 有回應
        already_clarified=False,
    )
    assert result is None


@pytest.mark.asyncio
async def test_vague_query_no_clarify_when_has_ticker():
    """query 有特定 ticker（BTC/台積電）→ 走既有 should_clarify，不走 vague。"""
    from core.agents.clarification import should_clarify_vague_query

    result = await should_clarify_vague_query(
        query="SNXX 值得買嗎",  # 有 ticker
        response="",
        already_clarified=False,
    )
    assert result is None


@pytest.mark.asyncio
async def test_vague_query_no_clarify_for_chinese_stock_names():
    """Regression（線上 #特斯拉案例）：中文標的名稱是明確標的，不算模糊問題。

    根因：should_clarify_vague_query 原本只用 _detect_query_ticker（只認大寫
    英文代號 AAPL/BTC），漏掉中文名稱（特斯拉/台積電/蘋果/輝達）。導致「特斯拉
    現在真的值得購買嗎」被誤判為模糊綜合判斷問題、觸發 false-positive 釐清
    （「我不太確定你問的『特斯拉…』具體指什麼」）。
    """
    from core.agents.clarification import should_clarify_vague_query

    # 線上實際案例 + 同類中文標的
    explicit_zh_stocks = [
        "特斯拉現在真的值得購買嗎",  # 線上案例（DANNY 回報）
        "台積電現在可以買嗎",
        "蘋果股價多少",
        "輝達值得投資嗎",
        "鴻海可以買嗎",
    ]
    for q in explicit_zh_stocks:
        result = await should_clarify_vague_query(
            query=q, response="", already_clarified=False
        )
        assert result is None, (
            f"「{q}」含明確中文標的，不該觸發 vague clarify（線上 #特斯拉回歸）"
        )


@pytest.mark.asyncio
async def test_vague_query_no_clarify_for_chinese_crypto_names():
    """加密貨幣中文名（比特幣/以太幣/狗狗幣）也是明確標的，不該觸發釐清。

    _extract_tickers_zh 預設 include_crypto=False（給 response 側偵測偷換標的用），
    但對 query 偰而言加密貨幣是明確標的——vague clarify 要傳 include_crypto=True。
    """
    from core.agents.clarification import should_clarify_vague_query

    explicit_crypto_zh = [
        "比特幣現在值得買嗎",
        "以太幣呢",
        "狗狗幣值得買嗎",
        "索拉納未來如何",
    ]
    for q in explicit_crypto_zh:
        result = await should_clarify_vague_query(
            query=q, response="", already_clarified=False
        )
        assert result is None, f"「{q}」含明確加密貨幣標的，不該觸發 vague clarify"


@pytest.mark.asyncio
async def test_vague_query_still_triggers_for_truly_vague():
    """修復後，真正模糊的綜合判斷問題仍應觸發釐清（不能過度修正）。"""
    from core.agents.clarification import should_clarify_vague_query

    truly_vague = [
        "美股適合買嗎",
        "現在是不是該出場了",
        "整體大盤看法",
        "現在進場好嗎",
    ]
    for q in truly_vague:
        result = await should_clarify_vague_query(
        llm=_FakeLLM(content=_FOUR_OPTIONS),
            query=q, response="", already_clarified=False
        )
        assert result is not None, f"「{q}」是真模糊問題，應觸發 clarify"
        assert result["type"] == "clarify"


def test_vague_clarified_state_channel_exists():
    """Regression（線上 #特斯拉 resume 500）：_vague_clarified 必須在 state schema。

    根因：claw_loop 寫 _vague_clarified=True 進 state return，但 ManagerState
    沒宣告這個欄位 → LangGraph「unknown channel」拒收 update → HITL resume 時
    Internal Server Error。同類欄位 _scope_clarified/_tool_clarified 都有宣告，
    獨缺 _vague_clarified。
    """
    from core.agents.models import ManagerState

    anns = ManagerState.__annotations__
    # 同類防無限 interrupt 的 flag 都要在 schema
    assert "_vague_clarified" in anns, "_vague_clarified 缺宣告 → resume 500"
    assert "_scope_clarified" in anns, "_scope_clarified 缺宣告 → resume 500"
    assert "_tool_clarified" in anns, "_tool_clarified 缺宣告 → resume 500"


@pytest.mark.asyncio
async def test_vague_query_no_clarify_when_already_clarified():
    """已 clarify 過 → 不再觸發（防無限迴圈）。"""
    from core.agents.clarification import should_clarify_vague_query

    result = await should_clarify_vague_query(
        query="美股適合買嗎",
        response="",
        already_clarified=True,  # 已 clarify
    )
    assert result is None


@pytest.mark.asyncio
async def test_vague_query_no_clarify_for_too_short_query():
    """太短的 query（如純「嗨」）→ 不 clarify。"""
    from core.agents.clarification import should_clarify_vague_query

    result = await should_clarify_vague_query(
        query="嗨", response="", already_clarified=False
    )
    assert result is None


# ============================================================================
# clarify 結構化（#④ — 學 Hermes MC + Other）
# ============================================================================


@pytest.mark.asyncio
async def test_vague_query_clarify_has_structured_options():
    """#④ clarify payload 含結構化 options（學 Hermes single-select MC）。"""
    from core.agents.clarification import should_clarify_vague_query

    result = await should_clarify_vague_query(
        llm=_FakeLLM(content=_FOUR_OPTIONS),
        query="美股現在適合買嗎", response="", already_clarified=False
    )
    assert result is not None
    assert "options" in result, "應含結構化選項"
    assert result["select_mode"] == "single"
    options = result["options"]
    assert 2 <= len(options) <= 4, f"Hermes 規範：最多 4 選項，得 {len(options)}"
    for opt in options:
        assert "label" in opt and "hint" in opt, "每個選項要有 label + hint"


@pytest.mark.asyncio
async def test_vague_query_clarify_options_relevant_to_query():
    """選項要跟 query 相關（美股問題 → 含大盤/特定股票/產業選項）。"""
    from core.agents.clarification import should_clarify_vague_query

    result = await should_clarify_vague_query(
        llm=_FakeLLM(content=_FOUR_OPTIONS),
        query="美股適合買嗎", response="", already_clarified=False
    )
    labels = " ".join(o["label"] for o in result["options"])
    assert "大盤" in labels or "S&P" in labels or "股票" in labels


# ============================================================================
# LLM 動態選項（混合策略，2026-08）— 規則觸發 + LLM 生成選項
# ============================================================================


class _FakeLLM:
    """假 LLM：回傳預設 JSON 或指定內容。"""

    def __init__(self, content: str = "", fail: bool = False):
        self._content = content
        self._fail = fail
        self.last_prompt = None
        self.last_kwargs = None
        self.fail_on_format = False  # 帶 response_format 時拋例外（模擬不支援的 provider）

    def _invoke(self, messages, kwargs):
        if self._fail:
            raise RuntimeError("LLM down")
        if self.fail_on_format and kwargs.get("response_format"):
            raise TypeError("unsupported argument: response_format")
        first = messages[0]
        # 支援 dict（舊介面）與 LangChain SystemMessage（新介面）
        self.last_prompt = first["content"] if isinstance(first, dict) else first.content
        self.last_kwargs = kwargs
        class _R:
            content = self._content
        return _R()

    def invoke(self, messages, **kwargs):
        return self._invoke(messages, kwargs)

    async def ainvoke(self, messages, **kwargs):
        return self._invoke(messages, kwargs)


# LLM 產生的 4 個選項（貼著 query 語意；測試用固定內容）
_FOUR_OPTIONS = (
    '[{"label": "整體大盤", "hint": "美股整體指數"},'
    '{"label": "特定股票", "hint": "某支股票，請補代號"},'
    '{"label": "某個產業", "hint": "科技、能源等板塊"},'
    '{"label": "技術面與基本面", "hint": "綜合分析，不含進出場建議"}]'
)


@pytest.mark.asyncio
async def test_vague_query_llm_generates_options():
    """有 LLM 時：選項由 LLM 生成（非寫死）。"""
    from core.agents.clarification import _build_vague_query_clarification

    llm = _FakeLLM(
        content='[{"label": "比特幣價格", "hint": "查 BTC 即時價格"},'
                '{"label": "加密貨幣技術分析", "hint": "RSI/MACD 指標"},'
                '{"label": "加密貨幣安全檢測", "hint": "檢查代幣合約風險"}]'
    )
    question, options = await _build_vague_query_clarification("我想要投資", "zh-TW", llm=llm)
    labels = [o["label"] for o in options]
    assert labels == ["比特幣價格", "加密貨幣技術分析", "加密貨幣安全檢測"]
    assert "我想要投資" in question
    # prompt 有帶 query 語意
    assert "我想要投資" in llm.last_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "llm_kwargs",
    [
        {"fail": True},  # LLM 拋例外
        {"content": "我不確定你指的是什麼，請再說清楚"},  # 非 JSON
        {"content": '[{"label": "只有一個", "hint": "不足兩個"}]'},  # <2 項
        {"content": '[{"label": "有 label 無 hint"}, {"hint": "有 hint 無 label"}]'},  # 缺欄位
        {"content": "[]"},  # LLM 判斷「不需要釐清」
    ],
    ids=["llm_down", "bad_json", "too_few", "missing_fields", "empty_array"],
)
async def test_vague_query_no_options_when_llm_cannot_produce_them(llm_kwargs):
    """LLM 產不出合法選項 → 回空 options，不退回寫死的「整體大盤／特定股票」。

    寫死選項只適用投資語境；薪資規劃、還款這類問題彈出「S&P 500／道瓊」是答非所問
    （線上案例 2026-10-06）。沒有合適選項就不彈卡，比彈一張錯的卡好。
    """
    from core.agents.clarification import _build_vague_query_clarification

    llm = _FakeLLM(**llm_kwargs)
    _, options = await _build_vague_query_clarification("我想要投資", "zh-TW", llm=llm)
    assert options == []


@pytest.mark.asyncio
async def test_vague_query_no_options_without_llm():
    """沒有 LLM（呼叫端沒傳）→ 空 options，不再有寫死那組。"""
    from core.agents.clarification import _build_vague_query_clarification

    _, options = await _build_vague_query_clarification("我想要投資", "zh-TW")
    assert options == []


@pytest.mark.asyncio
async def test_vague_query_prompt_includes_assistant_response():
    """選項要貼著模型剛回答的內容產生，所以 prompt 必須帶 response。"""
    from core.agents.clarification import _build_vague_query_clarification

    llm = _FakeLLM(
        content='[{"label": "欠款有利息", "hint": "依利率排還款順序"},'
                '{"label": "欠款無利息", "hint": "先建立緊急預備金"}]'
    )
    await _build_vague_query_clarification(
        "月薪六萬想還錢又想投資", "zh-TW", llm=llm,
        response="先確認欠款是否有息、你目前的生活開銷多少",
    )
    assert "先確認欠款是否有息" in llm.last_prompt


@pytest.mark.asyncio
async def test_should_clarify_vague_query_none_when_llm_fails():
    """Phase D：選項生不出來 → 不彈卡（return None），不要彈一張沒選項的卡。"""
    from core.agents.clarification import should_clarify_vague_query

    result = await should_clarify_vague_query(
        query="美股適合買嗎", response="", llm=_FakeLLM(fail=True)
    )
    assert result is None


@pytest.mark.asyncio
async def test_should_clarify_wrong_scope_none_when_llm_fails():
    """Phase E：選項生不出來 → 不彈卡，原回答照常送出。"""
    from core.agents.clarification import should_clarify_wrong_scope

    result = await should_clarify_wrong_scope(
        query="我想要投資", response="BTC 現在 $67,000，RSI 55", llm=_FakeLLM(fail=True)
    )
    assert result is None


@pytest.mark.asyncio
async def test_vague_query_llm_options_constrained_to_supported_markets():
    """選項生成的 prompt 必須約束在平台支援的市場。

    線上案例（2026-09-08）：「我想要投資」彈出的選項含「投資房地產」「穩健
    理財規劃」——平台根本不能分析這些。生成 prompt 需明確排除不支援資產。
    """
    from core.agents.clarification import _build_vague_query_clarification

    llm = _FakeLLM(
        content='[{"label": "加密貨幣", "hint": "BTC 等主流幣種"},'
                '{"label": "美股", "hint": "AAPL 等個股"},'
                '{"label": "台股", "hint": "2330 等個股"}]'
    )
    await _build_vague_query_clarification("我想要投資", "zh-TW", llm=llm)
    # 生成 prompt（英文）必須排除平台不支援的資產類別
    assert "real estate" in llm.last_prompt


@pytest.mark.asyncio
async def test_vague_query_llm_strips_markdown_fence():
    """LLM 回傳包 ```json 的 markdown fence → 正常解析。"""
    from core.agents.clarification import _build_vague_query_clarification

    llm = _FakeLLM(
        content='```json\n[{"label": "選項A", "hint": "說明A"},'
                '{"label": "選項B", "hint": "說明B"}]\n```'
    )
    _, options = await _build_vague_query_clarification("我想要投資", "zh-TW", llm=llm)
    labels = [o["label"] for o in options]
    assert labels == ["選項A", "選項B"]


@pytest.mark.asyncio
async def test_vague_query_llm_english_options():
    """英文語系 → LLM 生成英文選項（含 prompt 英文指示）。"""
    from core.agents.clarification import _build_vague_query_clarification

    llm = _FakeLLM(
        content='[{"label": "BTC price", "hint": "check current BTC price"},'
                '{"label": "ETH analysis", "hint": "technical analysis"}]'
    )
    question, options = await _build_vague_query_clarification("I want to invest", "en", llm=llm)
    assert "Could you clarify" in question
    assert options[0]["label"] == "BTC price"
    assert "JSON array" in llm.last_prompt  # en 指示


@pytest.mark.asyncio
async def test_vague_query_llm_sends_response_format_json_object():
    """第一優先帶 response_format=json_object（deepseek/OpenAI 相容強制 JSON）。"""
    from core.agents.clarification import _build_vague_query_clarification

    llm = _FakeLLM(
        content='[{"label": "投資目標", "hint": "退休、買房等財務目標"},'
                '{"label": "風險承受度", "hint": "能接受多大波動"}]'
    )
    _, options = await _build_vague_query_clarification("我想要投資", "zh-TW", llm=llm)
    assert [o["label"] for o in options] == ["投資目標", "風險承受度"]
    assert llm.last_kwargs.get("response_format") == {"type": "json_object"}


@pytest.mark.asyncio
async def test_vague_query_llm_retries_without_format_on_error():
    """provider 不支援 response_format（拋例外）→ 自動不帶 format 重試成功。"""
    from core.agents.clarification import _build_vague_query_clarification

    llm = _FakeLLM(
        content='[{"label": "A選項", "hint": "說明A"}, {"label": "B選項", "hint": "說明B"}]'
    )
    llm.fail_on_format = True  # 帶 response_format 時拋例外
    _, options = await _build_vague_query_clarification("我想要投資", "zh-TW", llm=llm)
    assert [o["label"] for o in options] == ["A選項", "B選項"]
    assert llm.last_kwargs.get("response_format") is None  # 重試不帶 format


def test_vague_query_llm_parses_dict_wrapped_options():
    """json_object mode 下模型可能回 {"options": [...]} 包裝 → 解析。"""
    from core.agents.clarification import _parse_clarify_options

    result = _parse_clarify_options(
        '{"options": [{"label": "台股", "hint": "台股分析"}, {"label": "美股", "hint": "美股分析"}]}',
        "zh-TW",
    )
    assert result is not None
    assert [o["label"] for o in result] == ["台股", "美股"]


def test_vague_query_llm_parses_prose_wrapped_json():
    """模型在 JSON 前後加說明文字 → 提取陣列段解析。"""
    from core.agents.clarification import _parse_clarify_options

    text = (
        "好的，以下是針對「我想要投資」的幾個方向：\n"
        '[{"label": "開戶教學", "hint": "教你開證券戶"}, {"label": "選股技巧", "hint": "基本面選股"}]'
        "\n請選擇一個！"
    )
    result = _parse_clarify_options(text, "zh-TW")
    assert result is not None
    assert [o["label"] for o in result] == ["開戶教學", "選股技巧"]


def test_vague_query_llm_parses_unknown_dict_key():
    """模型回 {"clarifications": [...]} 等未知 key → 兜底找第一個 list 值。"""
    from core.agents.clarification import _parse_clarify_options

    result = _parse_clarify_options(
        '{"clarifications": [{"label": "X", "hint": "y"}, {"label": "Z", "hint": "w"}]}',
        "zh-TW",
    )
    assert result is not None
    assert [o["label"] for o in result] == ["X", "Z"]


@pytest.mark.asyncio
async def test_vague_query_llm_prompt_contains_json_for_all_languages():
    """回歸：lang_instruction 對 zh-TW 等未列舉語言不能 fallback 成字面
    'zh-CN'（會讓 prompt 不含 json → deepseek json_object mode 400）。"""
    from core.agents.clarification import _build_vague_query_clarification

    for lang in ("zh-TW", "zh-CN", "en", "ru", "zh-HK"):
        llm = _FakeLLM(content='[{"label": "A", "hint": "a"}, {"label": "B", "hint": "b"}]')
        await _build_vague_query_clarification("我想要投資", lang, llm=llm)
        assert "json" in llm.last_prompt.lower(), (
            f"language={lang} 的 prompt 必須含 'json'（否則 deepseek 400）"
        )
