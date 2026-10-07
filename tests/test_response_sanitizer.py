"""_clean_claw_response 的測試 — 特別是 tool name 移除。

背景：LLM 把內部工具名稱（get_crypto_price、us_stock_price 等）寫進回應給
使用者看，暴露內部架構。_clean_claw_response 負責後處理移除這些。
"""
from __future__ import annotations

import pytest

from core.agents.manager.claw_loop import _clean_claw_response

# ============================================================================
# Tool name 移除
# ============================================================================


@pytest.mark.parametrize(
    "leak_text,should_not_contain",
    [
        ("BTC 現價 64000【get_crypto_price】", "get_crypto_price"),
        ("RSI [technical_analysis] = 51.46", "technical_analysis"),
        ("來源：us_stock_price", "us_stock_price"),
        ("新聞來源（google_news）", "google_news"),
        (r"數據 `us_technical_analysis` 顯示", "us_technical_analysis"),
        ("Source: aggregate_news", "aggregate_news"),
        ("(resolve_symbol) 確認代號", "resolve_symbol"),
        ("資料來源：web_search", "web_search"),
        ("資料來源：get_commodity_price", "get_commodity_price"),
        ("【load_skill】載入方法", "load_skill"),
    ],
)
def test_tool_name_removed(leak_text, should_not_contain):
    """所有 tool name 洩漏模式都該被移除。"""
    cleaned = _clean_claw_response(leak_text)
    assert should_not_contain not in cleaned, (
        f"清後仍含 {should_not_contain!r}: {cleaned!r}"
    )


def test_normal_text_preserved():
    """正常無 tool name 的文字不該被破壞。"""
    text = "比特幣現價 64000 美元，建議分批佈局。"
    assert _clean_claw_response(text) == text


def test_markdown_table_preserved():
    """Markdown 表格結構不該被破壞。"""
    text = "| 指標 | 數值 |\n|------|------|\n| RSI | 51.46 |"
    cleaned = _clean_claw_response(text)
    assert "| 指標 | 數值 |" in cleaned
    assert "51.46" in cleaned


def test_source_replaced_with_generic():
    """「來源：tool_name」應被替換為「來源：即時工具查詢」。"""
    cleaned = _clean_claw_response("資料來源：us_stock_price")
    assert "即時工具查詢" in cleaned
    assert "us_stock_price" not in cleaned


def test_source_replaced_english():
    cleaned = _clean_claw_response("Source: aggregate_news")
    assert "us_stock_price" not in cleaned
    assert "aggregate_news" not in cleaned


def test_multiple_leaks_in_one_response():
    """一段回應含多個 tool name 都該清。"""
    text = (
        "價格【get_crypto_price】為 64000，"
        "技術面[us_technical_analysis] RSI=51，"
        "新聞（google_news）無重大消息。"
    )
    cleaned = _clean_claw_response(text)
    for name in ["get_crypto_price", "us_technical_analysis", "google_news"]:
        assert name not in cleaned, f"仍含 {name!r}"


def test_full_width_parens_handled():
    """全形括號（name）也該被處理。"""
    cleaned = _clean_claw_response("來源（google_news）")
    assert "google_news" not in cleaned


def test_empty_brackets_cleaned():
    """tool name 移除後殘留的空括號也該清。"""
    cleaned = _clean_claw_response("BTC【get_crypto_price】現價 64000")
    assert "【】" not in cleaned
    assert "()" not in cleaned


# ============================================================================
# 既有清理功能不退化
# ============================================================================


def test_fake_urls_removed():
    text = "詳見 https://example.com/fake"
    cleaned = _clean_claw_response(text)
    assert "example.com" not in cleaned


def test_agent_tags_removed():
    text = "[crypto] BTC 分析..."
    cleaned = _clean_claw_response(text)
    assert "[crypto]" not in cleaned


def test_empty_links_fixed():
    text = "[點擊查看]()"
    cleaned = _clean_claw_response(text)
    assert "()" not in cleaned
    assert "點擊查看" in cleaned


# ============================================================================
# 裸 tool name（無包圍符號）— 2026-07-20 新增
# ============================================================================


def test_bare_tool_name_removed():
    """裸 tool name 在句中（無括號/引號）也該被移除。"""
    text = "我用了 get_crypto_price 查到 BTC 現價 64000。"
    cleaned = _clean_claw_response(text)
    assert "get_crypto_price" not in cleaned
    assert "64000" in cleaned


def test_bare_tool_name_at_sentence_start():
    """裸 tool name 在句首。"""
    text = "get_crypto_price 回傳 64000"
    cleaned = _clean_claw_response(text)
    assert "get_crypto_price" not in cleaned
    assert "64000" in cleaned


def test_bare_tool_name_at_sentence_end():
    """裸 tool name 在句尾。"""
    text = "價格來源是 get_crypto_price"
    cleaned = _clean_claw_response(text)
    assert "get_crypto_price" not in cleaned


def test_bare_tool_name_multiple_in_sentence():
    """一句話含多個裸 tool name。"""
    text = "先呼叫 resolve_symbol 確認代號，再用 get_crypto_price 查價格。"
    cleaned = _clean_claw_response(text)
    assert "resolve_symbol" not in cleaned
    assert "get_crypto_price" not in cleaned


def test_bare_tool_name_with_tool_suffix():
    """LangChain _tool 後綴變體也該被清。"""
    text = "資料來自 get_crypto_price_tool"
    cleaned = _clean_claw_response(text)
    assert "get_crypto_price_tool" not in cleaned
    assert "get_crypto_price" not in cleaned


def test_bare_tool_name_long_name():
    """較長的 tool name 也該被清。"""
    text = "技術分析來自 technical_analysis 模組"
    cleaned = _clean_claw_response(text)
    assert "technical_analysis" not in cleaned


def test_bare_tool_name_in_english_sentence():
    """英文句子中的 tool name。"""
    text = "I called resolve_symbol to check the ticker."
    cleaned = _clean_claw_response(text)
    assert "resolve_symbol" not in cleaned
    assert "ticker" in cleaned


# ============================================================================
# False positive — 正常文字不該被誤殺
# ============================================================================


def test_normal_english_not_removed():
    """正常英文該完整保留（不該把無關詞誤清）。"""
    text = "Bitcoin is trading higher today on strong volume."
    cleaned = _clean_claw_response(text)
    # 完整保留（沒任何 tool name 在裡面）
    assert "Bitcoin" in cleaned
    assert "trading higher" in cleaned


def test_chinese_finance_terms_preserved():
    """金融中文專有名詞不該被誤清。"""
    text = "技術指標 RSI 偏多，建議逢低買進。"
    cleaned = _clean_claw_response(text)
    assert "RSI" in cleaned
    assert "偏多" in cleaned


def test_word_containing_tool_substring_not_removed():
    """含 tool name 子字串的正常詞不該被誤清（邊界判斷）。"""
    # 'my_get_crypto_price_thing' 含 'get_crypto_price' 但前後有 word char
    # 該被 lookbehind/lookahead 擋下，不該被清
    text = "my_get_crypto_price_thing is a custom variable"
    cleaned = _clean_claw_response(text)
    # 因為前後有底線（word char），不該被當成 tool name 清掉
    assert "my_get_crypto_price_thing" in cleaned


# ============================================================================
# Chat-template special token / scaffolding 清洗 — 2026-07-23 新增
# ============================================================================


@pytest.mark.parametrize(
    "token",
    [
        "<|im_start|>",
        "<|im_end|>",
        "<|system|>",
        "<|user|>",
        "<|assistant|>",
        "<|begin_of_text|>",
        "<|endoftext|>",
        "<system-reminder>",
        "</system-reminder>",
        "<system>",
        "</system>",
        "<tool_call>",
        "</tool_call>",
        "<function_call>",
        "</function_calls>",
        "<start_of_turn>",
        "<end_of_turn>",
    ],
)
def test_special_token_removed(token):
    """每個 chat-template special token 都該被清掉，正常內容保留。"""
    text = f"{token}比特幣現價 64000{token}"
    cleaned = _clean_claw_response(text)
    assert token not in cleaned, f"清後仍含 {token!r}: {cleaned!r}"
    assert "64000" in cleaned


def test_special_token_with_role_label():
    """ChatML 常見形式：<|im_start|>system\\n... <|im_end|> 也該被清。"""
    text = "<|im_start|>system\n你是加密分析師\n<|im_end|>今天 BTC 上漲。"
    cleaned = _clean_claw_response(text)
    for token in ["<|im_start|>", "<|im_end|>"]:
        assert token not in cleaned
    assert "今天 BTC 上漲" in cleaned


def test_tool_call_scaffolding_removed():
    """function-calling scaffolding（<tool_call>...</tool_call>）也該被清。"""
    text = "<tool_call>{\"name\": \"get_crypto_price\"}</tool_call>價格是 64000。"
    cleaned = _clean_claw_response(text)
    assert "<tool_call>" not in cleaned
    assert "</tool_call>" not in cleaned
    assert "64000" in cleaned


def test_normal_brackets_not_confused_with_special_token():
    """普通角括號內容（非 special token）不該被誤清。"""
    # 例如「< 100」這類數學符號或普通 HTML 標籤不該被清
    text = "跌幅 < 100 點，收在 <strong>高點</strong>。"
    cleaned = _clean_claw_response(text)
    assert "< 100" in cleaned or "<100" in cleaned
    assert "<strong>" in cleaned  # 普通標籤保留


def test_special_token_removal_preserves_around_text():
    """token 夾在中間，前後文字都要保留。"""
    text = "前段文字<|im_end|>後段文字"
    cleaned = _clean_claw_response(text)
    assert "前段文字" in cleaned
    assert "後段文字" in cleaned
    assert "<|im_end|>" not in cleaned
