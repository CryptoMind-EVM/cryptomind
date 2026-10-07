"""工具呼叫防護測試 — 去重 + 輪數上限。

業界共識（Dev.to「Prevent Reasoning Loops」、n8n「max tool interactions」、
DeepSeek agent 指南）：reasoning agent 必須有去重 + 輪數上限，否則會陷入
「過度思考 → 反覆呼叫相同/相似工具」循環，吃光 timeout。

線上 #特斯拉案例（DANNY Zeabur）：LLM 連續兩輪搜尋「Tesla Q2 2026 earnings
revenue」幾乎相同的 query，每輪又觸發 Jina 抓 16000 字，跑滿 300s 超時、0 token。
"""

from __future__ import annotations

import pytest

from core.agents.tool_guard import (
    _TOOL_TURN_LIMITS,
    check_tool_guard,
    reset_turn_tool_guard,
)


@pytest.fixture(autouse=True)
def _fresh_turn():
    """每個測試前重設 turn 狀態（隔離）。"""
    reset_turn_tool_guard()
    yield
    reset_turn_tool_guard()


# ============================================================================
# 去重 — 完全相同 query
# ============================================================================


def test_exact_duplicate_query_blocked():
    """完全相同的 query 第二次呼叫必須被攔截。"""
    b1, _ = check_tool_guard("web_search", query="比特幣價格")
    b2, m2 = check_tool_guard("web_search", query="比特幣價格")
    assert b1 is False
    assert b2 is True
    assert "重複" in m2 or "相同" in m2


def test_exact_duplicate_whitespace_normalized():
    """空白不同的 query 視為相同（normalize 後指紋一致）。"""
    check_tool_guard("web_search", query="Tesla  earnings")
    b, _ = check_tool_guard("web_search", query="Tesla earnings")
    assert b is True, "空白不同但內容相同的 query 應被去重"


def test_exact_duplicate_case_insensitive():
    """大小寫不同的 query 視為相同。"""
    check_tool_guard("web_search", query="Bitcoin Price")
    b, _ = check_tool_guard("web_search", query="bitcoin price")
    assert b is True


# ============================================================================
# 去重 — 相似 query（語義重疊）
# ============================================================================


def test_similar_query_tesla_case_blocked():
    """Regression（線上 #特斯拉案例）：語義相同但用詞分散的 query 要被攔截。

    log 實測兩個 query：
      - "Tesla TSLA Q2 2026 earnings results revenue delivery numbers"
      - "Tesla Q2 2026 revenue EPS gross margin specific numbers earnings report"
    純 Jaccard≈0.39（低於門檻），靠核心實體共享（tesla + earnings/revenue）偵測。
    """
    q1 = "Tesla TSLA Q2 2026 earnings results revenue delivery numbers"
    q2 = "Tesla Q2 2026 revenue EPS gross margin specific numbers earnings report"
    b1, _ = check_tool_guard("web_search", query=q1)
    b2, _ = check_tool_guard("web_search", query=q2)
    assert b1 is False, "第一次搜尋不該被擋"
    assert b2 is True, "相似 query（同標的同主題）第二次必須被擋（線上 #特斯拉回歸）"


def test_similar_query_high_jaccard_blocked():
    """純詞重疊高（Jaccard ≥ 門檻）也要被擋。"""
    check_tool_guard("web_search", query="比特幣價格分析")
    b, _ = check_tool_guard("web_search", query="比特幣價格預測")
    assert b is True


def test_different_company_same_topic_not_blocked():
    """不同公司 + 同主題（都查財報）不該被擋 — 標的實體不同。"""
    check_tool_guard("web_search", query="Apple AAPL earnings Q3 2026")
    b, _ = check_tool_guard("web_search", query="Microsoft MSFT earnings Q3 2026")
    assert b is False, "不同公司的同類查詢不該被去重"


def test_same_company_different_topic_not_blocked():
    """同公司 + 不同主題（價格 vs 安全事件）不該被擋 — 主題詞不重疊。"""
    check_tool_guard("web_search", query="Tesla TSLA stock price today")
    b, _ = check_tool_guard("web_search", query="Tesla TSLA security hack vulnerability")
    assert b is False, "同公司不同主題的查詢不該被去重"


# ============================================================================
# 輪數上限
# ============================================================================


def test_web_search_turn_limit():
    """web_search 每 turn 上限 = _TOOL_TURN_LIMITS['web_search']。"""
    limit = _TOOL_TURN_LIMITS["web_search"]
    for i in range(limit):
        b, _ = check_tool_guard("web_search", query=f"query_{i}")
        assert b is False, f"第{i+1}次（未達上限 {limit}）不該被擋"
    # 超過上限
    b, m = check_tool_guard("web_search", query="query_over_limit")
    assert b is True, f"第 {limit+1} 次必須被擋（達上限）"
    assert "上限" in m


def test_fetch_url_turn_limit():
    """fetch_url 每 turn 上限 = _TOOL_TURN_LIMITS['fetch_url']。"""
    limit = _TOOL_TURN_LIMITS["fetch_url"]
    for i in range(limit):
        b, _ = check_tool_guard("fetch_url", url=f"https://example.com/{i}")
        assert b is False
    b, _ = check_tool_guard("fetch_url", url="https://example.com/over")
    assert b is True


def test_unlimited_tool_not_blocked():
    """不在限制清單的工具（如 get_crypto_price）永遠放行。"""
    for _ in range(20):
        b, m = check_tool_guard("get_crypto_price", symbol="BTC")
        assert b is False
        assert m == ""


# ============================================================================
# reset_turn_tool_guard — 每 turn 重設
# ============================================================================


def test_reset_clears_counts():
    """reset 後計數器歸零，可重新呼叫。"""
    limit = _TOOL_TURN_LIMITS["web_search"]
    # 用到上限
    for i in range(limit):
        check_tool_guard("web_search", query=f"q{i}")
    b, _ = check_tool_guard("web_search", query="blocked")
    assert b is True
    # reset 後可再呼叫
    reset_turn_tool_guard()
    b, _ = check_tool_guard("web_search", query="after_reset")
    assert b is False, "reset 後計數器應歸零"


def test_reset_clears_dedup_window():
    """reset 後去重視窗清空，相同 query 可再查。"""
    check_tool_guard("web_search", query="比特幣")
    b, _ = check_tool_guard("web_search", query="比特幣")
    assert b is True
    reset_turn_tool_guard()
    b, _ = check_tool_guard("web_search", query="比特幣")
    assert b is False, "reset 後去重視窗應清空"


# ============================================================================
# 攔截訊息對 LLM 有指引性
# ============================================================================


def test_block_message_guides_llm_to_use_existing_data():
    """攔截訊息必須告訴 LLM「用現有資料回答」，而非只說錯誤。"""
    check_tool_guard("web_search", query="Tesla earnings")
    _, m = check_tool_guard("web_search", query="Tesla earnings")
    # 訊息要引導 LLM 用既有結果，不是只報錯
    assert "先前" in m or "現有" in m or "已經" in m, (
        "攔截訊息應引導 LLM 用既有結果回答，避免它困惑"
    )


def test_turn_limit_message_guides_alternative():
    """輪數上限訊息要告訴 LLM 該怎麼辦（用現有資料 / 告知不足）。"""
    limit = _TOOL_TURN_LIMITS["web_search"]
    for i in range(limit):
        check_tool_guard("web_search", query=f"q{i}")
    _, m = check_tool_guard("web_search", query="over")
    assert "現有" in m or "資料" in m, "上限訊息應引導用現有資料回答"
