"""test_guest_market_data.py — 訪客模式 Tier 1：唯讀市場數據（設計：docs/plans/2026-08-27-guest-mode-tier1-design.md）

涵蓋：
- 訪客可讀 /api/screener（快取路徑，refresh 強制 false 不觸發重算）
- 訪客可讀 /api/klines（mock get_klines，不碰網路）
- GUEST_DATA_ACCESS=off → 訪客 401；登入用戶不受 flag 影響
- 成本端點維持鎖：/api/market-pulse/refresh-all 訪客 401
- /api/market-pulse/{symbol} 訪客可讀公開快取（設計決策：回快取可看）
- /api/guest/quota 回應含 data_access flag（前端解鎖依據）
"""

from __future__ import annotations

import pytest

from api.routers import guest as guest_router
from api.routers.market import rest as market_rest
from core import shared_cache

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _reset_caches():
    shared_cache.reset()
    from api.middleware.rate_limit import limiter

    limiter.reset()
    guest_router._local_quota.clear()
    guest_router._local_global.clear()
    yield
    shared_cache.reset()
    limiter.reset()
    guest_router._local_quota.clear()
    guest_router._local_global.clear()


@pytest.fixture
def cached_screener(monkeypatch):
    """快取命中樁：訪客/登入用戶都不碰上游數據源。"""
    payload = {"data": [{"symbol": "BTC", "price": 60000}], "cached_at": "stub"}
    monkeypatch.setattr(market_rest, "try_get_cached_screener", lambda refresh: payload)
    return payload


@pytest.fixture
def no_background_screener(monkeypatch):
    """哨兵：若訪客觸發重算路徑（run_default_screener）直接失敗。"""

    def _boom(*a, **kw):  # pragma: no cover - 觸發即測試失敗
        raise AssertionError("guest must not trigger background screener recompute")

    monkeypatch.setattr(market_rest, "run_default_screener", _boom)


# ---------------------------------------------------------------- screener


class TestGuestScreener:
    async def test_guest_reads_cached_screener(self, client, cached_screener):
        resp = await client.post(
            "/api/screener", json={"exchange": "binance", "refresh": False}
        )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["symbol"] == "BTC"

    async def test_guest_refresh_is_coerced_to_cache(
        self, client, cached_screener, no_background_screener
    ):
        """訪客帶 refresh=true 也只讀快取——防止清 cookie 灌重算。"""
        resp = await client.post(
            "/api/screener", json={"exchange": "binance", "refresh": True}
        )
        assert resp.status_code == 200
        assert resp.json() == cached_screener

    async def test_flag_off_blocks_guest(self, client, monkeypatch):
        monkeypatch.setenv("GUEST_DATA_ACCESS", "false")
        resp = await client.post(
            "/api/screener", json={"exchange": "binance", "refresh": False}
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------- klines


class TestGuestKlines:
    async def test_guest_reads_klines(self, client, monkeypatch):
        import pandas as pd

        df = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp(2026, 8, 27),
                    "open": 100.0,
                    "high": 110.0,
                    "low": 95.0,
                    "close": 105.0,
                    "volume": 12.0,
                }
            ]
        )
        monkeypatch.setattr(market_rest, "get_klines", lambda **kw: df)
        resp = await client.post(
            "/api/klines",
            json={
                "symbol": "BTC",
                "exchange": "binance",
                "interval": "1d",
                "limit": 1,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "BTC"
        assert body["klines"][0]["close"] == 105.0

    async def test_flag_off_blocks_guest(self, client, monkeypatch):
        monkeypatch.setenv("GUEST_DATA_ACCESS", "false")
        resp = await client.post(
            "/api/klines",
            json={
                "symbol": "BTC",
                "exchange": "binance",
                "interval": "1d",
                "limit": 1,
            },
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------- 邊界


class TestCostEndpointsStayGated:
    async def test_guest_cannot_refresh_market_pulse(self, client, monkeypatch):
        """LLM 成本端點對訪客維持 401（回歸保護；關 TEST_MODE 同正式環境）。"""
        import core.config as cfg

        monkeypatch.setattr(cfg, "TEST_MODE", False)
        resp = await client.post("/api/market-pulse/refresh-all", json={})
        assert resp.status_code == 401

    async def test_guest_reads_cached_market_pulse(self, client, monkeypatch):
        """設計決策：pulse/{symbol} 對訪客回公開快取（不觸發 LLM）。"""
        payload = {"symbol": "BTCUSDT", "summary": "cached pulse"}
        monkeypatch.setattr(market_rest, "try_get_cached_pulse", lambda *a, **kw: dict(payload))
        resp = await client.get("/api/market-pulse/BTCUSDT")
        assert resp.status_code == 200
        assert resp.json()["summary"] == "cached pulse"


class TestQuotaCarriesDataFlag:
    async def test_quota_includes_data_access(self, client, monkeypatch):
        monkeypatch.setenv("GUEST_DATA_ACCESS", "true")
        resp = await client.get("/api/guest/quota")
        assert resp.status_code == 200
        assert resp.json()["data_access"] is True

    async def test_quota_flag_off(self, client, monkeypatch):
        monkeypatch.setenv("GUEST_DATA_ACCESS", "false")
        resp = await client.get("/api/guest/quota")
        assert resp.json()["data_access"] is False
