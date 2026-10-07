"""
test_tool_system_v2.py — Phase 0 + 1 改良驗證

Phase 0: BTC 預設值移除（symbol 必填）
Phase 1.1: get_crypto_price CoinGecko fallback
Phase 1.2: resolve_symbol 工具
Phase 1.4: get_pi_price deprecated 標記
"""

from __future__ import annotations

import json

import pytest


@pytest.mark.unit
class TestPhase0_RequiredSymbol:
    """Phase 0：5 個工具的 symbol 改為必填，無 BTC 預設值。"""

    def test_get_crypto_price_symbol_required(self):
        from core.agents.tools import get_crypto_price

        field = get_crypto_price.args_schema.model_fields["symbol"]
        assert field.is_required(), "symbol must be required (no default)"

    def test_google_news_symbol_required(self):
        from core.agents.tools import google_news

        field = google_news.args_schema.model_fields["symbol"]
        assert field.is_required()

    def test_aggregate_news_symbol_required(self):
        from core.agents.tools import aggregate_news

        field = aggregate_news.args_schema.model_fields["symbol"]
        assert field.is_required()

    def test_technical_analysis_symbol_required(self):
        from core.agents.tools import technical_analysis

        field = technical_analysis.args_schema.model_fields["symbol"]
        assert field.is_required()

    def test_get_futures_data_symbol_required(self):
        from core.agents.tools import get_futures_data

        field = get_futures_data.args_schema.model_fields["symbol"]
        assert field.is_required()


@pytest.mark.unit
class TestPhase1_ResolveSymbol:
    """Phase 1.2：resolve_symbol 工具註冊與基本介面。"""

    def test_resolve_symbol_registered_in_all_tools(self):
        from core.agents.tools import ALL_TOOLS, resolve_symbol

        assert resolve_symbol in ALL_TOOLS, "resolve_symbol must be in ALL_TOOLS"

    def test_resolve_symbol_signature(self):
        from core.agents.tools import resolve_symbol

        field = resolve_symbol.args_schema.model_fields["query"]
        assert field.is_required(), "query must be required"

    def test_resolve_symbol_returns_json_string(self):
        """無論成功失敗，都回 JSON 字串。"""
        from core.agents.tools import resolve_symbol

        result = resolve_symbol.invoke({"query": "nonexistent_coin_xyz_9999"})
        # 應該回 JSON 字串，含 symbol 或 error
        data = json.loads(result)
        assert "symbol" in data, "response must contain 'symbol' field"


@pytest.mark.unit
class TestPhase1_BootstrapRegistration:
    """確認 bootstrap 註冊 resolve_symbol 給 crypto/chat/manager。"""

    @pytest.fixture
    def manager(self):
        from langchain_openai import ChatOpenAI

        from core.agents.bootstrap import bootstrap

        return bootstrap(
            ChatOpenAI(api_key="sk-fake", model="gpt-4o-mini"),
            web_mode=True,
            language="zh-TW",
            user_tier="free",
            user_id="_test_v2_boot",
            session_id="_test_v2_boot",
        )

    def test_resolve_symbol_registered(self, manager):
        assert "resolve_symbol" in manager.tool_registry._tools

    def test_resolve_symbol_in_crypto_agent_tools(self, manager):
        tools = manager.tool_registry.list_for_agent("crypto")
        assert any(t.name == "resolve_symbol" for t in tools)


@pytest.mark.integration
class TestPhase1_CoinGeckoFallback:
    """Phase 1.1：CoinGecko fallback live test（需要網路）。"""

    def test_btc_via_exchange(self):
        from core.tools.crypto_modules.analysis import get_crypto_price_tool

        result = get_crypto_price_tool.invoke({"symbol": "BTC"})
        # 應該拿到價格（無論主流交易所或 fallback）
        assert "錯誤" not in result
        assert "BTC" in result.upper()
        assert "$" in result or "USD" in result

    def test_pi_via_fallback_or_exchange(self):
        from core.tools.crypto_modules.analysis import get_crypto_price_tool

        result = get_crypto_price_tool.invoke({"symbol": "PI"})
        # PI 在 OKX 上市，但可能會走 fallback，都應該成功
        assert "PI" in result.upper()
        assert "$" in result

    def test_resolve_symbol_english_names(self):
        """CoinGecko search API handles English names; Chinese → English is LLM's job."""
        from core.agents.tools import resolve_symbol

        for name, expected_symbol in [("Bitcoin", "BTC"), ("Dogecoin", "DOGE")]:
            result = resolve_symbol.invoke({"query": name})
            data = json.loads(result)
            assert data.get("symbol") == expected_symbol, (
                f"'{name}' should resolve to {expected_symbol}, got {data}"
            )

    def test_resolve_symbol_english_passthrough(self):
        from core.agents.tools import resolve_symbol

        result = resolve_symbol.invoke({"query": "Bitcoin"})
        data = json.loads(result)
        assert data.get("symbol") == "BTC"
