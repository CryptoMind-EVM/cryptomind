"""Unit tests for BYOK (Bring Your Own Key) tool key plumbing.

Covers:
- provider classification (llm vs tool) + frontend metadata
- key_resolver contextvar lifecycle + precedence (user key -> official env -> None)
- web_search routing (Tavily when key present, DuckDuckGo fallback otherwise)
"""

from unittest.mock import patch

import pytest

from core.orm.user_api_keys_repo import (
    LLM_PROVIDERS,
    TOOL_PROVIDERS,
    provider_kind,
    tool_provider_meta,
)
from core.tools import key_resolver


@pytest.mark.unit
def test_provider_kind_classification():
    assert provider_kind("openai") == "llm"
    assert provider_kind("tavily") == "tool"
    # Unknown providers default to llm (back-compat)
    assert provider_kind("unknown_provider") == "llm"


@pytest.mark.unit
def test_provider_lists_are_disjoint():
    assert set(LLM_PROVIDERS).isdisjoint(TOOL_PROVIDERS)
    assert "tavily" in TOOL_PROVIDERS
    assert "openai" in LLM_PROVIDERS


@pytest.mark.unit
def test_tool_provider_meta_shape():
    meta = tool_provider_meta()
    assert "tavily" in meta
    entry = meta["tavily"]
    assert entry["provider"] == "tavily"
    assert "display_name" in entry
    assert "signup_url" in entry


@pytest.mark.unit
def test_contextvar_set_get_reset():
    token = key_resolver.set_current_user_id("user-42")
    try:
        assert key_resolver.get_current_user_id() == "user-42"
    finally:
        key_resolver.reset_current_user_id(token)
    assert key_resolver.get_current_user_id() is None


@pytest.mark.unit
def test_resolve_tool_key_prefers_user_key():
    token = key_resolver.set_current_user_id("user-1")
    try:
        with patch.object(
            key_resolver, "_get_user_tool_key", return_value="user-key-123"
        ) as mock_get:
            result = key_resolver.resolve_tool_key("tavily", official_env="TAVILY_KEY")
        assert result == "user-key-123"
        mock_get.assert_called_once_with("user-1", "tavily")
    finally:
        key_resolver.reset_current_user_id(token)


@pytest.mark.unit
def test_resolve_tool_key_falls_back_to_official_env(monkeypatch):
    monkeypatch.setenv("TAVILY_OFFICIAL", "official-key")
    token = key_resolver.set_current_user_id("user-1")
    try:
        with patch.object(key_resolver, "_get_user_tool_key", return_value=None):
            result = key_resolver.resolve_tool_key(
                "tavily", official_env="TAVILY_OFFICIAL"
            )
        assert result == "official-key"
    finally:
        key_resolver.reset_current_user_id(token)


@pytest.mark.unit
def test_resolve_tool_key_returns_none_when_byok_and_no_user_key():
    token = key_resolver.set_current_user_id("user-1")
    try:
        with patch.object(key_resolver, "_get_user_tool_key", return_value=None):
            # official_env=None means forced BYOK, no fallback
            result = key_resolver.resolve_tool_key("tavily", official_env=None)
        assert result is None
    finally:
        key_resolver.reset_current_user_id(token)


@pytest.mark.unit
def test_resolve_tool_key_no_user_returns_none():
    # No contextvar user set, forced BYOK
    assert key_resolver.resolve_tool_key("tavily", official_env=None) is None


@pytest.mark.unit
def test_search_web_uses_tavily_when_key_present():
    from core.tools import web_search

    with (
        patch.object(key_resolver, "resolve_tool_key", return_value="tav-key"),
        patch.object(
            web_search,
            "search_tavily",
            return_value=[{"title": "t", "link": "l", "snippet": "s"}],
        ) as mock_tavily,
        patch.object(web_search, "search_duckduckgo") as mock_ddg,
    ):
        results = web_search.search_web("bitcoin", max_results=3)

    assert results and results[0]["title"] == "t"
    mock_tavily.assert_called_once()
    mock_ddg.assert_not_called()


@pytest.mark.unit
def test_search_web_falls_back_to_ddg_without_key():
    from core.tools import web_search

    with (
        patch.object(key_resolver, "resolve_tool_key", return_value=None),
        patch.object(web_search, "search_tavily") as mock_tavily,
        patch.object(
            web_search,
            "search_duckduckgo",
            return_value=[{"title": "d", "link": "l", "snippet": "s"}],
        ) as mock_ddg,
    ):
        results = web_search.search_web("bitcoin", max_results=3)

    assert results and results[0]["title"] == "d"
    mock_tavily.assert_not_called()
    mock_ddg.assert_called_once()


@pytest.mark.unit
def test_search_web_tavily_empty_falls_back_to_ddg():
    from core.tools import web_search

    with (
        patch.object(key_resolver, "resolve_tool_key", return_value="tav-key"),
        patch.object(web_search, "search_tavily", return_value=[]),
        patch.object(
            web_search,
            "search_duckduckgo",
            return_value=[{"title": "d", "link": "l", "snippet": "s"}],
        ) as mock_ddg,
    ):
        results = web_search.search_web("bitcoin", max_results=3)

    assert results and results[0]["title"] == "d"
    mock_ddg.assert_called_once()


# ── New BYOK tool providers (fred / coinmarketcap / etherscan) ──────────────


@pytest.mark.unit
def test_new_tool_providers_registered():
    for provider in ("fred", "coinmarketcap", "etherscan"):
        assert provider in TOOL_PROVIDERS
        assert provider_kind(provider) == "tool"
    meta = tool_provider_meta()
    for provider in ("fred", "coinmarketcap", "etherscan"):
        assert provider in meta
        assert meta[provider]["signup_url"]


@pytest.mark.unit
def test_cmc_quote_missing_key_returns_hint():
    from core.tools import coinmarketcap_tools

    with patch.object(key_resolver, "resolve_tool_key", return_value=None):
        result = coinmarketcap_tools.get_cmc_quote.invoke({"symbol": "BTC"})
    assert "error" in result
    assert "CoinMarketCap" in result["error"]


@pytest.mark.unit
def test_central_bank_rates_missing_fred_key_returns_hint():
    from core.tools import forex_tools

    with patch.object(key_resolver, "resolve_tool_key", return_value=None):
        result = forex_tools.get_central_bank_rates.invoke({})
    assert "error" in result
    assert "FRED" in result["error"]


@pytest.mark.unit
def test_eth_balance_without_key_returns_redirect_message():
    from core.tools.crypto_modules import etherscan

    with patch.object(key_resolver, "resolve_tool_key", return_value=None):
        result = etherscan.get_eth_balance.invoke({"address": "0x" + "a" * 40})
    # No key → guidance message pointing to Etherscan website / settings
    assert "etherscan.io" in result.lower()


@pytest.mark.unit
def test_eth_balance_invalid_address():
    from core.tools.crypto_modules import etherscan

    result = etherscan.get_eth_balance.invoke({"address": "not-an-address"})
    assert "Invalid" in result


# ── GoPlus Security tools ───────────────────────────────────────────────────


@pytest.mark.unit
def test_token_security_invalid_address():
    from core.tools.crypto_modules import goplus

    result = goplus.check_token_security.invoke({"contract_address": "not-an-address"})
    assert "Invalid" in result


@pytest.mark.unit
def test_address_safety_without_official_key_returns_hint(monkeypatch):
    """check_address_safety 在平台未設 GOPLUS_APP_KEY 時 → 回「尚未設定」訊息。"""
    from core.tools.crypto_modules import goplus

    # 模擬平台未設定官方 key
    monkeypatch.delenv("GOPLUS_APP_KEY", raising=False)
    monkeypatch.delenv("GOPLUS_APP_SECRET", raising=False)
    # 清除 token 快取
    goplus._cached_token = None
    goplus._cached_token_expires = 0.0

    result = goplus.check_address_safety.invoke({"address": "0x" + "a" * 40})
    assert "尚未設定" in result or "GoPlus" in result


@pytest.mark.unit
def test_address_safety_invalid_address():
    from core.tools.crypto_modules import goplus

    result = goplus.check_address_safety.invoke({"address": "bad"})
    assert "Invalid" in result


# ── Serper / FMP BYOK 接線（2026-09-22：修復兩段永遠跑不到的死碼）────────────
#
# search_web() 一直有 serper / fmp 的分支，但這兩個 provider 沒進 TOOL_PROVIDERS，
# 而 save 路徑會擋掉不在 SUPPORTED_PROVIDERS 的 provider——使用者永遠存不進金鑰，
# resolve_tool_key() 永遠回 None。下面的測試守著「存得進去 + 找得到入口」。


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["serper", "fmp"])
def test_search_providers_registered(provider):
    """provider 要在清單裡，存金鑰才不會被 SUPPORTED_PROVIDERS 擋掉。"""
    assert provider in TOOL_PROVIDERS
    assert provider_kind(provider) == "tool"


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["serper", "fmp"])
def test_search_providers_have_signup_meta(provider):
    """前端要顯示「去哪申請」，缺 signup_url 等於給了輸入框卻沒說 key 哪來。"""
    meta = tool_provider_meta()
    assert provider in meta
    assert meta[provider]["signup_url"].startswith("https://")
    assert meta[provider]["display_name"]


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["serper", "fmp"])
def test_search_providers_have_key_tester(provider):
    """存完金鑰要能當場測試有效性，否則失敗時 search_web 只會靜默回 []。"""
    from core.tools.key_tester import test_provider_key

    result = test_provider_key(provider, "")
    # 空金鑰不該打外部 API，直接回「沒給金鑰」而不是「不支援此 provider」
    assert result["success"] is False
    assert "No API key" in result["message"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "tool_id,provider",
    [("serper_search_source", "serper"), ("fmp_search_source", "fmp")],
)
def test_search_source_seed_entries(tool_id, provider):
    """金鑰輸入框由 tool.key_provider 驅動，所以每個 provider 要有自己的 seed 條目
    （同 cryptopanic_news_source / newsapi_news_source 的模式）。"""
    from core.database.tools import _TOOLS_SEED

    entry = next((t for t in _TOOLS_SEED if t["tool_id"] == tool_id), None)
    assert entry is not None, f"{tool_id} 不在 _TOOLS_SEED，UI 上不會出現輸入框"
    assert entry["key_provider"] == provider
    assert entry["key_mode"] == "byok"


@pytest.mark.unit
@pytest.mark.parametrize("tool_id", ["serper_search_source", "fmp_search_source"])
def test_search_source_translated(tool_id):
    """4 語都要有，否則英俄 UI 會 fallback 顯示繁中。"""
    from core.agents.tool_name_translations import _TRANSLATIONS

    assert tool_id in _TRANSLATIONS
    for lang in ("en", "zh-CN", "ru"):
        assert _TRANSLATIONS[tool_id][lang]["name"]
        assert _TRANSLATIONS[tool_id][lang]["desc"]
