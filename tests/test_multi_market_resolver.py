"""Multi-market resolver 測試。

驗證 ``resolve_symbol_all_markets`` 對 ambiguous ticker 的處理：

- AKE 案例：應偵測到加密 Akedo + 未支援的法股 Arkema + delisted ASX
- BTC：單一市場命中，不觸發 ambiguity
- 純查無的 ticker：空 candidates
- web_search 偵測未支援市場

不打真實 API（mock SymbolNormalizer）— focus 在邏輯正確性。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.tools.multi_market_resolver import (
    _UNSUPPORTED_MARKET_INFO,
    MarketCandidate,
    resolve_symbol_all_markets_async,
    resolve_symbol_all_markets_sync,
)

# ============================================================================
# Mock helpers
# ============================================================================


def _make_candidate(market, symbol=None, verified=False, source=None, name=None):
    return MarketCandidate(
        market=market,
        symbol=symbol,
        verified=verified,
        source=source,
        name=name,
        status="active" if symbol else None,
    )


# ============================================================================
# Async resolver（核心邏輯）
# ============================================================================


@pytest.mark.asyncio
async def test_btc_single_match_not_ambiguous():
    """BTC 只在 crypto 命中 → ambiguous=False。"""
    from core.tools import multi_market_resolver as mod

    async def fake_probe(query, market):
        if market == "crypto":
            return _make_candidate("crypto", symbol="bitcoin", verified=True, source="coingecko")
        return MarketCandidate(market=market, symbol=None, error="not_found")

    async def fake_web(query):
        return []

    with patch.object(mod, "_probe_single_market_async", side_effect=fake_probe), \
         patch.object(mod, "_detect_unsupported_market_candidates", side_effect=fake_web):
        result = await resolve_symbol_all_markets_async("BTC")

    assert result["query"] == "BTC"
    assert result["ambiguous"] is False
    assert result["valid_count"] == 1
    candidates = result["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["market"] == "crypto"
    assert candidates[0]["symbol"] == "bitcoin"
    assert candidates[0]["verified"] is True


@pytest.mark.asyncio
async def test_ake_ambiguous_multiple_markets():
    """AKE 在 crypto + us 都命中 → ambiguous=True。"""
    from core.tools import multi_market_resolver as mod

    async def fake_probe(query, market):
        if market == "crypto":
            return _make_candidate("crypto", symbol="akedo", verified=True, source="coingecko")
        if market == "us":
            return _make_candidate("us", symbol="AKE", verified=False, source="llm_confident")
        return MarketCandidate(market=market, symbol=None, error="not_found")

    async def fake_web(query):
        # 模擬 web_search 偵測到 AKE 也在法股市場
        return [
            MarketCandidate(
                market="fr",
                symbol=None,
                source="web_search",
                verified=False,
                supported=False,
                status="detected_via_web_search",
                note=_UNSUPPORTED_MARKET_INFO["fr"]["note_zh"],
            )
        ]

    with patch.object(mod, "_probe_single_market_async", side_effect=fake_probe), \
         patch.object(mod, "_detect_unsupported_market_candidates", side_effect=fake_web):
        result = await resolve_symbol_all_markets_async("AKE")

    assert result["ambiguous"] is True
    assert result["valid_count"] == 2  # crypto + us
    assert result["unsupported_count"] == 1  # fr
    markets = {c["market"] for c in result["candidates"]}
    assert "crypto" in markets
    assert "us" in markets
    assert "fr" in markets


@pytest.mark.asyncio
async def test_completely_unknown_ticker_no_candidates():
    """完全查無的 ticker（XYZFAKE）→ 空 candidates, ambiguous=False。"""
    from core.tools import multi_market_resolver as mod

    async def fake_probe(query, market):
        return MarketCandidate(market=market, symbol=None, error="not_found")

    async def fake_web(query):
        return []

    with patch.object(mod, "_probe_single_market_async", side_effect=fake_probe), \
         patch.object(mod, "_detect_unsupported_market_candidates", side_effect=fake_web):
        result = await resolve_symbol_all_markets_async("XYZFAKE")

    assert result["ambiguous"] is False
    assert result["valid_count"] == 0
    assert result["unsupported_count"] == 0
    assert result["candidates"] == []


@pytest.mark.asyncio
async def test_only_unsupported_market_matched():
    """只在未支援市場命中（例如只在法股找到）→ ambiguous=False（valid=0），
    但 recommendation 應告知使用者該 ticker 存在但不支援。
    """
    from core.tools import multi_market_resolver as mod

    async def fake_probe(query, market):
        return MarketCandidate(market=market, symbol=None, error="not_found")

    async def fake_web(query):
        return [
            MarketCandidate(
                market="de",
                symbol=None,
                source="web_search",
                verified=False,
                supported=False,
                status="detected_via_web_search",
                note=_UNSUPPORTED_MARKET_INFO["de"]["note_zh"],
            )
        ]

    with patch.object(mod, "_probe_single_market_async", side_effect=fake_probe), \
         patch.object(mod, "_detect_unsupported_market_candidates", side_effect=fake_web):
        result = await resolve_symbol_all_markets_async("FAKEDE")

    assert result["ambiguous"] is False  # 只 1 個 unsupported，不算 ambiguous
    assert result["valid_count"] == 0
    assert result["unsupported_count"] == 1
    assert "doesn't support" in result["recommendation"] or "不支援" in result["recommendation"] or "unsupported" in result["recommendation"]


@pytest.mark.asyncio
async def test_empty_query_returns_empty():
    """空 query → error。"""
    result = await resolve_symbol_all_markets_async("")
    assert result["candidates"] == []
    assert result["ambiguous"] is False
    assert "error" in result


# ============================================================================
# Sync wrapper（給 LangChain tool 用）
# ============================================================================


def test_sync_wrapper_returns_valid_json():
    """sync wrapper 應回合法 JSON 字串。"""
    from core.tools import multi_market_resolver as mod

    async def fake_probe(query, market):
        if market == "crypto":
            return _make_candidate("crypto", symbol="btc", verified=True)
        return MarketCandidate(market=market, symbol=None, error="not_found")

    async def fake_web(query):
        return []

    with patch.object(mod, "_probe_single_market_async", side_effect=fake_probe), \
         patch.object(mod, "_detect_unsupported_market_candidates", side_effect=fake_web):
        result_str = resolve_symbol_all_markets_sync("BTC")

    # 必須是合法 JSON
    data = json.loads(result_str)
    assert data["query"] == "BTC"
    assert len(data["candidates"]) == 1


def test_sync_wrapper_handles_resolver_failure():
    """resolver 內部炸掉 → 仍回合法 JSON（含 error 欄位），不 raise。"""
    from core.tools import multi_market_resolver as mod

    def boom(*args, **kwargs):
        raise RuntimeError("simulated API failure")

    with patch.object(mod, "resolve_symbol_all_markets_async", side_effect=boom):
        # 需要 patch sync wrapper 內部的呼叫 — 直接呼叫會炸，但 wrapper 應 catch
        # 因為 wrapper 是 asyncio.run，例外會被 wrapper 的 try/except 接住
        result_str = resolve_symbol_all_markets_sync("ANYTHING")

    data = json.loads(result_str)
    assert "error" in data
    assert "resolver_failed" in data["error"]


# ============================================================================
# MarketCandidate dataclass
# ============================================================================


def test_market_candidate_default_supported_true():
    """預設 supported=True（本平台支援的市場）。"""
    c = MarketCandidate(market="crypto", symbol="btc")
    assert c.supported is True


def test_market_candidate_unsupported_market():
    """未支援市場的 candidate 應明確 supported=False。"""
    c = MarketCandidate(
        market="fr", symbol=None, supported=False, note="法股不支援"
    )
    assert c.supported is False
    assert c.note == "法股不支援"


# ============================================================================
# 第 1 層：全球符號主檔（_lookup_global_symbol）
# 補 company_aliases.json 的英文缺口：全球用戶問 NVDA/MSFT 等英文 ticker
# 本地秒查，不用走外部 API。
# ============================================================================


def test_lookup_global_symbol_finds_us_ticker():
    """NVDA 等美股 ticker 在 global_symbols.json 命中（第 1 層）。"""
    from core.tools.multi_market_resolver import _lookup_global_symbol

    result = _lookup_global_symbol("NVDA")
    assert result is not None
    assert result.symbol == "NVDA"
    assert result.market == "us"
    assert "NVIDIA" in result.name
    assert result.source == "global_symbols"


def test_lookup_global_symbol_case_insensitive():
    """大小寫不敏感（ticker 慣例大寫，但用戶可能輸入小寫）。"""
    from core.tools.multi_market_resolver import _lookup_global_symbol

    assert _lookup_global_symbol("nvda") is not None
    assert _lookup_global_symbol("msft") is not None


def test_lookup_global_symbol_returns_none_for_unknown():
    """不在 global_symbols.json 的 ticker 回 None（fallback 到外部 API）。"""
    from core.tools.multi_market_resolver import _lookup_global_symbol

    assert _lookup_global_symbol("SNXXNOTREAL") is None


def test_global_symbols_covers_previously_missing():
    """覆蓋原本 company_aliases.json 沒有的高頻英文 ticker。

    這些是「全球用戶會問但中文 alias 表沒有」的——global_symbols.json 補上。
    """
    from core.tools.multi_market_resolver import _lookup_global_symbol

    for sym in ["MSFT", "JPM", "TSM", "AAPL", "AMZN", "GOOGL", "META"]:
        assert _lookup_global_symbol(sym) is not None, f"{sym} 應在 global_symbols.json"
