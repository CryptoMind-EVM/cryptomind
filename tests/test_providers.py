"""
test_providers.py — 統一 Provider 層測試

涵蓋：
- Symbol normalization 邏輯
- Factory dispatch（正確的 Provider 對應正確的市場）
- Singleton 行為
- Cache 行為（TTL）
- 介面契約（所有 Provider 必須有相同的 method signature）
- 整合測試（需要網路）：跨市場真實抓取

執行：
    pytest tests/test_providers.py              # unit only
    pytest tests/test_providers.py -m integration  # 含網路測試
"""

from __future__ import annotations

import time

import pytest

from core.providers import (
    SUPPORTED_MARKETS,
    StockDataProvider,
    clear_provider_cache,
    get_provider,
)
from core.providers.base_provider import normalize_symbol
from core.providers.twse_provider import TWSEProvider
from core.providers.us_provider import USProvider
from core.providers.yahoo_provider import YahooFinanceProvider

# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests — Symbol Normalization
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSymbolNormalization:
    """各市場 symbol 後綴自動處理。"""

    def test_us_keeps_plain_symbol(self):
        assert normalize_symbol("AAPL", "us") == "AAPL"
        assert normalize_symbol("nvda", "us") == "NVDA"  # uppercased

    def test_tw_adds_tw_suffix_for_digits(self):
        assert normalize_symbol("2330", "tw") == "2330.TW"
        assert normalize_symbol("0050", "tw") == "0050.TW"

    def test_tw_preserves_existing_suffix(self):
        assert normalize_symbol("2330.TW", "tw") == "2330.TW"
        assert normalize_symbol("2330.TWO", "tw") == "2330.TWO"

    def test_hk_adds_hk_suffix(self):
        assert normalize_symbol("0700", "hk") == "0700.HK"
        assert normalize_symbol("9988", "hk") == "9988.HK"

    def test_jp_adds_t_suffix(self):
        assert normalize_symbol("7203", "jp") == "7203.T"

    def test_kr_adds_ks_suffix(self):
        assert normalize_symbol("005930", "kr") == "005930.KS"

    def test_in_handles_alpha_symbols(self):
        assert normalize_symbol("RELIANCE", "in") == "RELIANCE.NS"

    def test_cn_routes_by_prefix(self):
        # 上海：6/9 開頭
        assert normalize_symbol("600519", "cn") == "600519.SS"
        assert normalize_symbol("900001", "cn") == "900001.SS"
        # 深圳：其他
        assert normalize_symbol("000858", "cn") == "000858.SZ"
        assert normalize_symbol("300750", "cn") == "300750.SZ"

    def test_empty_input_returns_empty(self):
        assert normalize_symbol("", "us") == ""


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests — Factory
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestProviderFactory:
    """factory dispatch 對應正確的 Provider class。"""

    def setup_method(self):
        clear_provider_cache()

    def test_us_dispatches_to_us_provider(self):
        provider = get_provider("us")
        assert isinstance(provider, USProvider)
        assert provider.market == "us"

    def test_tw_dispatches_to_twse_provider(self):
        provider = get_provider("tw")
        assert isinstance(provider, TWSEProvider)
        assert provider.market == "tw"

    @pytest.mark.parametrize("market", ["hk", "jp", "kr", "in", "cn"])
    def test_other_markets_dispatch_to_yahoo(self, market: str):
        provider = get_provider(market)
        assert isinstance(provider, YahooFinanceProvider)
        assert provider.market == market

    def test_supported_markets_all_resolvable(self):
        for market in SUPPORTED_MARKETS:
            provider = get_provider(market)
            assert isinstance(provider, StockDataProvider)

    def test_unsupported_market_raises(self):
        with pytest.raises(ValueError, match="Unsupported market"):
            get_provider("xx")

    def test_singleton_per_market(self):
        a = get_provider("us")
        b = get_provider("us")
        assert a is b, "Provider should be singleton per market"

    def test_case_insensitive(self):
        a = get_provider("US")
        b = get_provider("us")
        assert a is b

    def test_clear_cache_recreates_instance(self):
        a = get_provider("us")
        clear_provider_cache()
        b = get_provider("us")
        assert a is not b


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests — Provider Cache
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestProviderCache:
    """Provider 內建的 TTL cache 行為。"""

    def test_cache_set_and_get(self):
        provider = YahooFinanceProvider(market="us")
        provider._cache_set("k", {"x": 1}, ttl=60)
        assert provider._cache_get("k") == {"x": 1}

    def test_cache_expires(self):
        provider = YahooFinanceProvider(market="us")
        # 直接設定為已過期的 entry（避免 ttl=0 在同一 microsecond 內仍為 valid）
        provider._cache["k"] = ("v", time.time() - 1)
        assert provider._cache_get("k") is None

    def test_clear_cache(self):
        provider = YahooFinanceProvider(market="us")
        provider._cache_set("k", "v", ttl=60)
        provider.clear_cache()
        assert provider._cache_get("k") is None


# ──────────────────────────────────────────────────────────────────────────────
# Unit Tests — 介面契約
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestProviderInterface:
    """每個 Provider 都必須實作完整的抽象介面。"""

    @pytest.mark.parametrize("market", list(SUPPORTED_MARKETS))
    def test_all_providers_implement_interface(self, market: str):
        clear_provider_cache()
        provider = get_provider(market)
        # 4 個必要 method
        assert callable(provider.get_price)
        assert callable(provider.get_technicals)
        assert callable(provider.get_fundamentals)
        # 3 個有預設實作 method
        assert callable(provider.get_extras)
        assert callable(provider.get_news)
        assert callable(provider.get_snapshot)
        # Helper
        assert callable(provider.normalize)

    def test_get_snapshot_returns_unified_structure(self):
        """Snapshot 結構應在所有市場一致：symbol/market/price/technicals/fundamentals/extras/news"""
        clear_provider_cache()
        # 用 mock 避免網路；只驗結構
        provider = YahooFinanceProvider(market="us")
        # patch 各 get_* 方法回 stub
        provider.get_price = lambda s: {"symbol": s, "price": 100.0}
        provider.get_technicals = lambda s: {"rsi": 50}
        provider.get_fundamentals = lambda s: {"pe_ratio": 20.0}
        provider.get_extras = lambda s: {}
        provider.get_news = lambda s, limit=5: []
        snap = provider.get_snapshot("AAPL")
        assert "symbol" in snap
        assert "market" in snap
        assert "price" in snap
        assert "technicals" in snap
        assert "fundamentals" in snap
        assert "extras" in snap
        assert "news" in snap


# ──────────────────────────────────────────────────────────────────────────────
# Integration Tests（需要網路）
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestProviderLive:
    """真實網路抓取測試。慢，CI 時用 marker 篩選。"""

    def test_us_price_aapl(self):
        provider = get_provider("us")
        data = provider.get_price("AAPL")
        assert "error" not in data
        assert isinstance(data["price"], (int, float))
        assert data["price"] > 0
        assert data.get("currency") == "USD"

    def test_us_extras_has_institutional(self):
        provider = get_provider("us")
        extras = provider.get_extras("AAPL")
        # Apple 一定有機構持倉資料
        assert "institutional_holders" in extras
        assert len(extras["institutional_holders"]["holders"]) > 0

    def test_tw_uses_twse_for_pe(self):
        provider = get_provider("tw")
        fund = provider.get_fundamentals("2330")
        # TWSE 官方 PE 應存在
        assert "pe_ratio" in fund

    def test_tw_extras_has_institutional(self):
        provider = get_provider("tw")
        extras = provider.get_extras("2330")
        assert extras.get("source") == "TWSE OpenAPI"
        # 三大法人資料可能因非交易日而缺失，所以不強制
        # 只驗 structure 正確
        assert isinstance(extras, dict)

    def test_hk_price(self):
        provider = get_provider("hk")
        data = provider.get_price("0700")
        assert "error" not in data
        assert data.get("currency") == "HKD"

    @pytest.mark.parametrize(
        "market,symbol",
        [
            ("jp", "7203"),
            ("kr", "005930"),
            ("in", "RELIANCE"),
        ],
    )
    def test_global_markets_price(self, market: str, symbol: str):
        provider = get_provider(market)
        data = provider.get_price(symbol)
        assert "error" not in data, f"{market}/{symbol} price failed: {data}"
        assert isinstance(data["price"], (int, float))


# ──────────────────────────────────────────────────────────────────────────────
# Regression Tests — yf_helpers backward compat
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestYFHelpersBackwardCompat:
    """確保 yf_helpers.fetch_extras_sync 仍可被呼叫（5 個 router 仰賴它）。"""

    def test_fetch_extras_sync_callable(self):
        from api.routers.yf_helpers import fetch_extras_sync

        assert callable(fetch_extras_sync)

    def test_fetch_extras_async_callable(self):
        import inspect

        from api.routers.yf_helpers import fetch_extras

        assert inspect.iscoroutinefunction(fetch_extras)


# ──────────────────────────────────────────────────────────────────────────────
# Regression Tests — Agent Tools 整合
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestAgentToolsRouting:
    """確認 agent tools 都能透過 Provider 拿到資料。"""

    def test_us_stock_price_tool(self):
        from core.tools.us_stock_tools import us_stock_price

        result = us_stock_price.invoke({"symbol": "AAPL"})
        assert "error" not in result
        assert "price" in result

    def test_tw_stock_snapshot_tool(self):
        from core.tools.tw_stock_tools import tw_stock_snapshot

        result = tw_stock_snapshot.invoke({"ticker": "2330"})
        assert "error" not in result
        assert "price" in result
        assert "extras" in result  # TWSE-specific

    def test_global_stock_snapshot_tool(self):
        from core.tools.global_stock_tools import global_stock_snapshot

        result = global_stock_snapshot.invoke({"symbol": "0700", "market": "hk"})
        assert "error" not in result
        assert result.get("market") == "Hong Kong"


@pytest.mark.integration
class TestTWIndividualToolsViaProvider:
    """所有 TW 個別 @tool 都應透過 Provider 拿到資料，且保持向後相容欄位。"""

    def test_tw_stock_price_returns_legacy_fields(self):
        from core.tools.tw_stock_tools import tw_stock_price

        r = tw_stock_price.invoke({"ticker": "2330"})
        assert "error" not in r
        # 向後相容欄位
        assert "ticker" in r
        assert "current_price" in r
        assert "prev_close" in r
        assert "change_pct" in r
        assert "recent_ohlcv" in r
        assert len(r["recent_ohlcv"]) > 0

    def test_tw_fundamentals_returns_legacy_fields(self):
        from core.tools.tw_stock_tools import tw_fundamentals

        r = tw_fundamentals.invoke({"ticker": "2330"})
        assert "error" not in r
        # 向後相容欄位
        for key in [
            "ticker",
            "company_name",
            "pe_ratio",
            "pb_ratio",
            "dividend_yield_pct",
            "eps_ttm",
            "52w_high",
            "52w_low",
        ]:
            assert key in r, f"Missing legacy field: {key}"

    def test_tw_institutional_returns_legacy_fields(self):
        from core.tools.tw_stock_tools import tw_institutional

        r = tw_institutional.invoke({"ticker": "2330"})
        # 向後相容欄位（即使無資料也應有 ticker + source）
        assert "ticker" in r
        assert "source" in r

    def test_tw_pe_ratio_returns_legacy_fields(self):
        from core.tools.tw_stock_tools import tw_pe_ratio

        r = tw_pe_ratio.invoke({"code": "2330"})
        # 向後相容：code/name/pe_ratio/dividend_yield/pb_ratio/source
        if "error" not in r:
            for key in [
                "code",
                "name",
                "pe_ratio",
                "dividend_yield",
                "pb_ratio",
                "source",
            ]:
                assert key in r, f"Missing legacy field: {key}"

    def test_tw_monthly_revenue_returns_legacy_fields(self):
        from core.tools.tw_stock_tools import tw_monthly_revenue

        r = tw_monthly_revenue.invoke({"code": "2330"})
        assert isinstance(r, list)
        if r and "error" not in r[0]:
            for key in ["code", "name", "ym", "current_revenue", "yoy_pct"]:
                assert key in r[0], f"Missing legacy field: {key}"

    def test_tw_dividend_info_returns_legacy_fields(self):
        from core.tools.tw_stock_tools import tw_dividend_info

        r = tw_dividend_info.invoke({"code": "2330"})
        assert isinstance(r, list)
        if r and "error" not in r[0]:
            for key in ["code", "name", "year", "cash_dividend", "stock_dividend"]:
                assert key in r[0], f"Missing legacy field: {key}"
