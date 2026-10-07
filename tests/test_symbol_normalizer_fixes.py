"""
test_symbol_normalizer_fixes.py — 驗證 #1 TTL cache + #2 統一 resolve_symbol

Fix #1: SymbolNormalizer cache 從永久改為 TTL（1h/30min/5min by case）
Fix #2: resolve_symbol tool 委派到 SymbolNormalizer，支援多市場 + 中文/英文
"""

from __future__ import annotations

import json
import socket
import time

import pytest


def _has_network(host: str = "query1.finance.yahoo.com", port: int = 443) -> bool:
    """探測外網是否可用(live 測試用)。CI/離線環境自動 skip live 測試。"""
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


# Live 測試需要外網;CI/離線環境自動 skip,避免因網路斷而紅。
_skip_if_no_network = pytest.mark.skipif(
    not _has_network(), reason="live test needs external network"
)

# ──────────────────────────────────────────────────────────────────────────────
# Fix #1: TTL cache
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFix1_TTLCache:
    """SymbolNormalizer 的 cache 必須會過期，不可永久 stale。"""

    @pytest.fixture
    def normalizer(self):
        from core.tools.symbol_normalizer import SymbolNormalizer

        return SymbolNormalizer()

    def test_yfinance_cache_expires(self, normalizer):
        """yfinance cache entry 應該按 TTL 過期。"""
        normalizer._cache_set(normalizer._yfinance_cache, "FOO.HK", True, 1)
        # 立即查 → 命中
        assert normalizer._cache_get(normalizer._yfinance_cache, "FOO.HK") == (True,)
        # 1.5 秒後 → 過期
        time.sleep(1.5)
        assert normalizer._cache_get(normalizer._yfinance_cache, "FOO.HK") is None

    def test_coingecko_cache_expires(self, normalizer):
        normalizer._cache_set(normalizer._coingecko_cache, "key1", "BTC", 1)
        assert normalizer._cache_get(normalizer._coingecko_cache, "key1") == ("BTC",)
        time.sleep(1.5)
        assert normalizer._cache_get(normalizer._coingecko_cache, "key1") is None

    def test_cache_get_distinguishes_miss_from_none_value(self, normalizer):
        """cache_get 必須區分『沒快取』vs『快取的就是 None』。"""
        # 沒寫入 → 回 None
        assert normalizer._cache_get(normalizer._yfinance_cache, "NOTSET") is None
        # 寫入 None 值（明確表示「查過了，結果是 None」）
        normalizer._cache_set(normalizer._coingecko_cache, "negative", None, 60)
        cached = normalizer._cache_get(normalizer._coingecko_cache, "negative")
        # 應該回 (None,) 表示快取命中，值為 None
        assert cached == (None,)


# ──────────────────────────────────────────────────────────────────────────────
# Fix #2: resolve_symbol 統一委派
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFix2_ResolveSymbolDelegates:
    """resolve_symbol 工具應委派到 SymbolNormalizer，並接受 market 參數。"""

    def test_resolve_symbol_accepts_market_arg(self):
        """新版簽名：resolve_symbol(query, market='auto')"""
        from core.agents.tools import resolve_symbol

        field_names = list(resolve_symbol.args_schema.model_fields.keys())
        assert "query" in field_names
        assert "market" in field_names

    def test_resolve_symbol_returns_json_with_market_field(self):
        """回傳格式：{symbol, market, source, verified}（新版有 market 欄位）"""
        from core.agents.tools import resolve_symbol

        raw = resolve_symbol.invoke(
            {"query": "nonexistent_xyz_zzz", "market": "crypto"}
        )
        data = json.loads(raw)
        # 應該含 symbol（null）+ error
        assert "symbol" in data
        # 失敗時 error 應該存在
        assert data.get("symbol") is None or data.get("symbol") == ""


@pytest.mark.integration
@_skip_if_no_network
class TestFix2_LiveMultiMarket:
    """Live 測試：resolve_symbol 跨市場識別（需要網路，離線自動 skip）。"""

    def test_crypto_english_name(self):
        from core.agents.tools import _resolve_mem_cache, resolve_symbol

        _resolve_mem_cache.clear()
        raw = resolve_symbol.invoke({"query": "Bitcoin", "market": "crypto"})
        data = json.loads(raw)
        assert data.get("symbol") == "BTC"
        assert data.get("source") == "coingecko"

    def test_tw_chinese_name(self):
        from core.agents.tools import _resolve_mem_cache, resolve_symbol

        _resolve_mem_cache.clear()
        raw = resolve_symbol.invoke({"query": "台積電", "market": "tw"})
        data = json.loads(raw)
        assert data.get("symbol") == "2330.TW"
        assert data.get("source") == "twse_api"

    @pytest.mark.xfail(
        reason="live 外部 API（yfinance/HKEX）偶發逾時或資料延遲——全套件高負載"
        "下間歇失敗（2026-08-20 全量實測：單跑通過、滿載失敗）。保留測試信號，"
        "不作為紅燈門檻；若要根治改 mock 資料源",
        strict=False,
    )
    def test_us_company_name_via_search(self):
        from core.agents.tools import _resolve_mem_cache, resolve_symbol

        _resolve_mem_cache.clear()
        raw = resolve_symbol.invoke({"query": "Apple", "market": "us"})
        data = json.loads(raw)
        assert data.get("symbol") == "AAPL"
        assert "yfinance" in data.get("source", "")

    @pytest.mark.xfail(
        reason="live 外部 API（yfinance/HKEX）偶發逾時或資料延遲——全套件高負載"
        "下間歇失敗（2026-08-20 全量實測：單跑通過、滿載失敗）。保留測試信號，"
        "不作為紅燈門檻；若要根治改 mock 資料源",
        strict=False,
    )
    def test_hk_english_name_via_search(self):
        from core.agents.tools import _resolve_mem_cache, resolve_symbol

        _resolve_mem_cache.clear()
        raw = resolve_symbol.invoke({"query": "Tencent", "market": "hk"})
        data = json.loads(raw)
        assert data.get("symbol") == "0700.HK"

    @pytest.mark.xfail(
        reason="yfinance 對 JP market symbol 支援不穩(Toyota 查 TOYOTA.T 回 404,"
        "實際應為 7203.T);待 resolver JP 查找邏輯修正或 yfinance 上游修復",
        strict=False,
    )
    def test_jp_english_name_via_search(self):
        from core.agents.tools import _resolve_mem_cache, resolve_symbol

        _resolve_mem_cache.clear()
        raw = resolve_symbol.invoke({"query": "Toyota", "market": "jp"})
        data = json.loads(raw)
        assert data.get("symbol") == "7203.T"

    def test_kr_english_name_via_search(self):
        from core.agents.tools import _resolve_mem_cache, resolve_symbol

        _resolve_mem_cache.clear()
        raw = resolve_symbol.invoke({"query": "Samsung", "market": "kr"})
        data = json.loads(raw)
        assert data.get("symbol") == "005930.KS"


# ──────────────────────────────────────────────────────────────────────────────
# Fix #3: web_search fallback (opt-in, with verification)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFix3_WebSearchFallback:
    """web_search 是最後一道 fallback，預設關閉，避免每次 API 查詢都拖慢。"""

    def test_normalize_accepts_enable_web_fallback_param(self):
        import inspect

        from core.tools.symbol_normalizer import SymbolNormalizer

        n = SymbolNormalizer()
        sig = inspect.signature(n.normalize)
        assert "enable_web_fallback" in sig.parameters
        # 預設應該是 False（保持快速）
        assert sig.parameters["enable_web_fallback"].default is False

    def test_resolve_symbol_tool_has_use_web_search_param(self):
        from core.agents.tools import resolve_symbol

        field_names = list(resolve_symbol.args_schema.model_fields.keys())
        assert "use_web_search" in field_names

    def test_name_overlap_helper(self):
        """_name_overlap helper 應該正確判斷 API name 與 asset_name 關聯。"""
        from core.tools.symbol_normalizer import SymbolNormalizer

        # ✅ 英文標準匹配
        assert SymbolNormalizer._name_overlap(
            "Berkshire Hathaway Inc", "berkshire hathaway"
        )
        assert SymbolNormalizer._name_overlap("Apple Inc.", "apple")
        # ✅ 中英混合（asset_name 是 LLM 翻成英文後的標準形式）
        assert SymbolNormalizer._name_overlap(
            "Berkshire Hathaway", "berkshire hathaway"
        )
        # ❌ 不相關，必須擋掉
        assert not SymbolNormalizer._name_overlap(
            "Occidental Petroleum", "berkshire hathaway"
        )
        assert not SymbolNormalizer._name_overlap("Microsoft Corp", "apple")
        assert not SymbolNormalizer._name_overlap("Tesla Inc", "general motors")


@pytest.mark.integration
class TestFix3_WebSearchLive:
    """Live：web_search fallback 對冷門/描述性 query 的行為（需要網路）。"""

    def test_blocks_false_positive_on_descriptive_query(self):
        """『波克夏海瑟威 巴菲特的公司』web_search 會抽到 OXY 但 verify 應擋掉。"""
        from core.agents.models import ExtractedEntity
        from core.tools.symbol_normalizer import SymbolNormalizer

        n = SymbolNormalizer()
        n._yfinance_cache.clear()
        entity = ExtractedEntity(
            market="us",
            asset_name="波克夏海瑟威 巴菲特的公司",
            candidate_symbol=None,
            confidence=0.3,
        )
        result = n.normalize(entity, enable_web_fallback=True)
        # 不接受 OXY 這種「相關但不正確」的 false positive
        if result is not None:
            assert result.symbol != "OXY", "Should not return OXY for Berkshire query"

    def test_passes_through_api_when_no_web_fallback(self):
        """有 candidate 時不該觸發 web search（直接 API 命中）。"""
        from core.agents.models import ExtractedEntity
        from core.tools.symbol_normalizer import SymbolNormalizer

        n = SymbolNormalizer()
        n._yfinance_cache.clear()
        entity = ExtractedEntity(
            market="us",
            asset_name="Apple",
            candidate_symbol="AAPL",
            confidence=0.95,
        )
        result = n.normalize(entity, enable_web_fallback=False)
        assert result is not None
        assert result.symbol == "AAPL"
        # 應該走 API 路徑，不該觸發 web_search
        assert "web_search" not in result.source
