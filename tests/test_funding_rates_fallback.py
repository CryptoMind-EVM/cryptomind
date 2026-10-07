"""
Tests for funding rate route fallback logic.

Covers the resilience layer added to fix recurring production toast
"Failed to load funding rates":
  - Live update timeout → serve in-memory cache
  - Live update failure → DB cache fallback
  - Both fail → graceful empty response (never HTTP 500)
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from api.globals import FUNDING_RATE_CACHE
from api.routers.market.rest import get_funding_rates


@pytest.fixture(autouse=True)
def _clear_cache():
    """每個測試前清空 in-memory cache，避免互相污染。"""
    FUNDING_RATE_CACHE.clear()
    yield
    FUNDING_RATE_CACHE.clear()


class TestGetFundingRatesFallback:
    """get_funding_rates 的容錯路徑——外部 API 不可靠時絕不 500。"""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_update(self):
        """in-memory cache 有資料時，不觸發外部 update。"""
        FUNDING_RATE_CACHE["data"] = {
            "BTC": {"fundingRate": 0.0001},
            "ETH": {"fundingRate": -0.0002},
        }
        FUNDING_RATE_CACHE["timestamp"] = "2026-08-01T00:00:00Z"

        with patch(
            "api.routers.market.rest.update_funding_rates", new=AsyncMock()
        ) as mock_update, patch(
            "api.routers.market.rest.load_funding_rate_cache_async",
            new=AsyncMock(),
        ) as mock_load:
            result = await get_funding_rates()

        mock_update.assert_not_called()
        mock_load.assert_not_called()
        assert result["total_count"] == 2
        # sort 降序：BTC(0.0001) > ETH(-0.0002)，兩筆都進 top_bullish（limit=5 預設）
        assert result["top_bullish"][0]["symbol"] == "BTC"
        assert len(result["top_bullish"]) == 2

    @pytest.mark.asyncio
    async def test_update_timeout_falls_back_to_db_cache(self):
        """live update 超時 → 不 raise，fallback DB cache → 回傳 DB 資料。"""
        db_data = {"SOL": {"fundingRate": 0.0005}}

        async def _load_db():
            FUNDING_RATE_CACHE["data"] = db_data
            FUNDING_RATE_CACHE["timestamp"] = "2026-08-01T00:00:00Z"
            return True

        # update 慢到必超時（wait_for timeout 在路由是 20s，這裡 sleep 1s + timeout 0.01）
        # 但我們不 patch 路由的 timeout，所以直接讓 update mock 拋 TimeoutError
        # 模擬 wait_for 超時後的行為
        with patch(
            "api.routers.market.rest.update_funding_rates",
            new=AsyncMock(side_effect=asyncio.TimeoutError()),
        ), patch(
            "api.routers.market.rest.load_funding_rate_cache_async", new=_load_db
        ):
            result = await get_funding_rates()

        assert result["total_count"] == 1
        assert result["top_bullish"][0]["symbol"] == "SOL"

    @pytest.mark.asyncio
    async def test_update_failure_db_empty_returns_graceful_empty(self):
        """live update 失敗 + DB 也空 → 回傳空資料，不 500。"""
        with patch(
            "api.routers.market.rest.update_funding_rates",
            new=AsyncMock(side_effect=RuntimeError("OKX down")),
        ), patch(
            "api.routers.market.rest.load_funding_rate_cache_async",
            new=AsyncMock(return_value=False),
        ):
            result = await get_funding_rates()

        # graceful：空資料而非例外
        assert result["total_count"] == 0
        assert result["top_bullish"] == []
        assert result["top_bearish"] == []

    @pytest.mark.asyncio
    async def test_update_exception_falls_back_to_db(self):
        """live update 拋例外（非 timeout）→ fallback DB cache → 有資料。"""
        db_data = {"BTC": {"fundingRate": 0.001}, "ETH": {"fundingRate": -0.001}}

        async def _load_db():
            FUNDING_RATE_CACHE["data"] = db_data
            FUNDING_RATE_CACHE["timestamp"] = "2026-08-01T00:00:00Z"
            return True

        with patch(
            "api.routers.market.rest.update_funding_rates",
            new=AsyncMock(side_effect=ConnectionError("network")),
        ), patch(
            "api.routers.market.rest.load_funding_rate_cache_async", new=_load_db
        ):
            result = await get_funding_rates()

        assert result["total_count"] == 2
