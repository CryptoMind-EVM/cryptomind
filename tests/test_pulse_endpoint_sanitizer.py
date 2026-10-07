"""deep_analysis_helper sanitizer 測試 — 9 個 pulse 端點共用。

背景：9 個 stock/commodity/forex pulse 端點透過 deep_analyze_generic()
走 raw client.invoke()，原本完全沒 sanitize。雖然 prompt 不含工具名，
但 BYOK 模型仍可能幻覺出 get_crypto_price / resolve_symbol 等名稱。

2026-07-20 修復：在 deep_analyze_generic() 回傳前加 _clean_claw_response。
本測試驗證此修復。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_pulse_sanitizer_removes_bracketed_tool_name():
    """LLM 回含【get_crypto_price】，deep_analyze_generic 該清掉。"""
    from api.routers import deep_analysis_helper

    # Mock LLM client 回含 tool name 的內容
    fake_response = MagicMock()
    fake_response.content = "BTC【get_crypto_price】現價 64000 美元。"

    fake_client = MagicMock()
    fake_client.invoke = MagicMock(return_value=fake_response)

    with patch(
        "utils.llm_client.create_llm_client_from_config",
        return_value=(fake_client, None),
    ):
        ai_text, ai_error = await deep_analysis_helper.deep_analyze_generic(
            symbol="BTC",
            context="some market data",
            llm_key="sk-test",
            llm_provider="openai",
            llm_model="gpt-4",
            language="zh-TW",
        )

    assert ai_error is None
    assert ai_text is not None
    assert "get_crypto_price" not in ai_text
    assert "64000" in ai_text


@pytest.mark.asyncio
async def test_pulse_sanitizer_removes_bare_tool_name():
    """LLM 裸寫 tool name 也該被清。"""
    from api.routers import deep_analysis_helper

    fake_response = MagicMock()
    fake_response.content = "我用了 get_crypto_price 查到 BTC 現價 64000。"

    fake_client = MagicMock()
    fake_client.invoke = MagicMock(return_value=fake_response)

    with patch(
        "utils.llm_client.create_llm_client_from_config",
        return_value=(fake_client, None),
    ):
        ai_text, _ = await deep_analysis_helper.deep_analyze_generic(
            symbol="BTC",
            context="data",
            llm_key="sk-test",
            llm_provider="openai",
            llm_model="gpt-4",
        )

    assert ai_text is not None
    assert "get_crypto_price" not in ai_text
    assert "64000" in ai_text


@pytest.mark.asyncio
async def test_pulse_sanitizer_preserves_normal_analysis():
    """正常分析報告不該被破壞。"""
    from api.routers import deep_analysis_helper

    normal_report = (
        "【趨勢概覽】\nBTC 目前在 64000 美元，接近 52 週高點。\n\n"
        "【技術面信號】\nRSI 65，偏多但未過熱。\n\n"
        "【基本面 / 籌碼】\n市值 1.2 兆美元。\n\n"
        "【短線展望】\n支撑 62000，壓力 66000。"
    )
    fake_response = MagicMock()
    fake_response.content = normal_report

    fake_client = MagicMock()
    fake_client.invoke = MagicMock(return_value=fake_response)

    with patch(
        "utils.llm_client.create_llm_client_from_config",
        return_value=(fake_client, None),
    ):
        ai_text, _ = await deep_analysis_helper.deep_analyze_generic(
            symbol="BTC",
            context="data",
            llm_key="sk-test",
            llm_provider="openai",
            llm_model="gpt-4",
        )

    assert ai_text is not None
    # 重要欄位都保留
    assert "64000" in ai_text
    assert "RSI 65" in ai_text
    assert "支撑 62000" in ai_text
    assert "壓力 66000" in ai_text


@pytest.mark.asyncio
async def test_pulse_sanitizer_handles_source_pattern():
    """LLM 寫「來源：us_stock_price」該改成「來源：即時工具查詢」。"""
    from api.routers import deep_analysis_helper

    fake_response = MagicMock()
    fake_response.content = "RSI 51；來源：us_stock_price；偏多。"

    fake_client = MagicMock()
    fake_client.invoke = MagicMock(return_value=fake_response)

    with patch(
        "utils.llm_client.create_llm_client_from_config",
        return_value=(fake_client, None),
    ):
        ai_text, _ = await deep_analysis_helper.deep_analyze_generic(
            symbol="AAPL",
            context="data",
            llm_key="sk-test",
            llm_provider="openai",
            llm_model="gpt-4",
        )

    assert ai_text is not None
    assert "us_stock_price" not in ai_text
    assert "即時工具查詢" in ai_text


@pytest.mark.asyncio
async def test_pulse_sanitizer_handles_llm_error():
    """LLM 錯誤時不該 crash，回 None + friendly message。"""
    from api.routers import deep_analysis_helper

    fake_client = MagicMock()
    fake_client.invoke = MagicMock(side_effect=Exception("rate limit 429"))

    with patch(
        "utils.llm_client.create_llm_client_from_config",
        return_value=(fake_client, None),
    ):
        ai_text, ai_error = await deep_analysis_helper.deep_analyze_generic(
            symbol="BTC",
            context="data",
            llm_key="sk-test",
            llm_provider="openai",
        )

    assert ai_text is None
    assert ai_error is not None
    assert "Request frequency" in ai_error or "quota" in ai_error.lower()
