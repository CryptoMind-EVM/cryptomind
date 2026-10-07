"""資金費率雙資料源 + 來源標示的回歸測試（2026-08-25）。

背景：系統裡同時存在「只有幣安」與「只有 OKX」的兩條資金費率路徑——
- Agent 工具 get_futures_data 只吃幣安（fapi），幣安被擋/掛掉工具直接失敗；
- 前端 /api/funding-rates 排行榜只吃 OKX（per-instrument fan-out，已知不可靠）。

修復方向：
1. get_futures_data：幣安為主、OKX 公開 API 為備，輸出動態標示實際來源；
2. update_funding_rates：幣安單次全量 API（一次拿全市場）為主、OKX 為備，
   FUNDING_RATE_CACHE 記錄 source，API 回應帶出；
3. 前端排行榜顯示資料來源（i18n 4 語系）。
"""

import pytest

pytestmark = [pytest.mark.unit]


# ── 測試用的假回應 ───────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


_BINANCE_ALL = [
    {
        "symbol": "BTCUSDT",
        "lastFundingRate": "0.00010000",
        "nextFundingTime": 1787600000000,
    },
    {
        "symbol": "ETHUSDT",
        "lastFundingRate": "-0.00020000",
        "nextFundingTime": 1787600000000,
    },
    # 季度交割合約（帶底線）應被排除
    {"symbol": "BTCUSDT_250926", "lastFundingRate": "0.001", "nextFundingTime": 1},
    # 非 USDT 本位應被排除
    {"symbol": "BTCBUSD", "lastFundingRate": "0.001", "nextFundingTime": 1},
]

_OKX_SINGLE = {
    "code": "0",
    "msg": "",
    "data": [
        {
            "instId": "BTC-USDT-SWAP",
            "fundingRate": "0.00011200",
            "nextFundingRate": "",
            "fundingTime": "1787599000000",
            "nextFundingTime": "1787602000000",
        }
    ],
}


# ── 1. Agent 工具：get_futures_data 雙源 + 動態來源 ─────────────────────────


def test_futures_data_binance_primary(monkeypatch):
    """幣安正常 → 用幣安資料，來源標示 Binance。"""
    from core.tools.crypto_modules import sentiment

    def fake_get(url, **kwargs):
        assert "binance.com" in url
        return _FakeResponse(payload={"lastFundingRate": "0.00010000"})

    monkeypatch.setattr(sentiment.httpx, "get", fake_get)
    out = sentiment.get_futures_data.func("BTC")
    assert "0.0100%" in out
    assert "Binance" in out
    assert "OKX" not in out


def test_futures_data_okx_fallback_when_binance_fails(monkeypatch):
    """幣安失敗（非 200 / 例外）→ 自动落 OKX，來源標示 OKX。"""
    from core.tools.crypto_modules import sentiment

    def fake_get(url, **kwargs):
        if "binance.com" in url:
            return _FakeResponse(status_code=451)  # 模擬 geo-block
        assert "okx.com" in url
        return _FakeResponse(payload=_OKX_SINGLE)

    monkeypatch.setattr(sentiment.httpx, "get", fake_get)
    out = sentiment.get_futures_data.func("BTC")
    assert "0.0112%" in out  # 0.000112 -> %
    assert "OKX" in out
    assert "Binance" not in out


def test_futures_data_both_sources_fail(monkeypatch):
    """雙源都失敗 → 回覆友善錯誤（不是未處理例外）。"""
    from core.tools.crypto_modules import sentiment

    def fake_get(url, **kwargs):
        return _FakeResponse(status_code=503)

    monkeypatch.setattr(sentiment.httpx, "get", fake_get)
    out = sentiment.get_futures_data.func("BTC")
    assert "No funding rate data" in out or "funding rate" in out.lower()


def test_futures_data_okx_fallback_on_network_error(monkeypatch):
    """幣安丟連線例外（非 HTTP 狀態）也要能落到 OKX。"""
    from core.tools.crypto_modules import sentiment

    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        if "binance.com" in url:
            raise sentiment.httpx.ConnectTimeout("blocked")
        return _FakeResponse(payload=_OKX_SINGLE)

    monkeypatch.setattr(sentiment.httpx, "get", fake_get)
    out = sentiment.get_futures_data.func("BTC")
    assert "OKX" in out
    assert calls["n"] == 2


# ── 2. 前端排行榜：update_funding_rates 幣安為主 ────────────────────────────


@pytest.fixture()
def _funding_cache_sandbox():
    """FUNDING_RATE_CACHE 是跨測試共用的 module dict——存還原現場。"""
    from api.globals import FUNDING_RATE_CACHE

    snapshot = dict(FUNDING_RATE_CACHE)
    FUNDING_RATE_CACHE.clear()
    yield FUNDING_RATE_CACHE
    FUNDING_RATE_CACHE.clear()
    FUNDING_RATE_CACHE.update(snapshot)


async def test_update_funding_rates_binance_primary(monkeypatch, _funding_cache_sandbox):
    """幣安單次全量成功 → cache 填入正確形狀 + source=binance，不碰 OKX。"""
    import api.services as services

    def fake_binance():
        return services.parse_binance_funding_rates(_BINANCE_ALL)

    okx_called = {"n": 0}

    class _FakeOKX:
        def get_all_funding_rates(self):
            okx_called["n"] += 1
            return {"error": "should not be called"}

    async def _no_save(silent=True):
        return None

    monkeypatch.setattr(services, "_fetch_all_funding_rates_binance", fake_binance)
    monkeypatch.setattr(services, "OKXAPIConnector", _FakeOKX)
    monkeypatch.setattr(services, "save_funding_rate_cache_async", _no_save)

    await services.update_funding_rates()

    data = _funding_cache_sandbox["data"]
    assert _funding_cache_sandbox["source"] == "binance"
    assert "BTC-USDT" in data and "ETH-USDT" in data
    assert "BTCUSDT" not in data  # key 是 OKX 相容的 BASE-USDT 格式
    assert "BTCUSDT_250926" not in data  # 交割合約被排除
    assert data["BTC-USDT"]["fundingRate"] == pytest.approx(0.01)  # 已轉 %
    assert data["ETH-USDT"]["fundingRate"] == pytest.approx(-0.02)
    assert okx_called["n"] == 0


async def test_update_funding_rates_okx_fallback(monkeypatch, _funding_cache_sandbox):
    """幣安全量失敗 → 落回 OKX fan-out，source=okx。"""
    import api.services as services

    def fake_binance():
        return None

    class _FakeOKX:
        def get_all_funding_rates(self):
            return {"BTC-USDT": {"fundingRate": 0.0112, "instId": "BTC-USDT-SWAP"}}

    async def _no_save(silent=True):
        return None

    monkeypatch.setattr(services, "_fetch_all_funding_rates_binance", fake_binance)
    monkeypatch.setattr(services, "OKXAPIConnector", _FakeOKX)
    monkeypatch.setattr(services, "save_funding_rate_cache_async", _no_save)

    await services.update_funding_rates()

    assert _funding_cache_sandbox["source"] == "okx"
    assert _funding_cache_sandbox["data"]["BTC-USDT"]["fundingRate"] == pytest.approx(0.0112)


def test_parse_binance_funding_rates_filters_and_converts():
    """單位轉換（小數→%）、BASE-USDT key、排除交割/非 USDT。"""
    import api.services as services

    parsed = services.parse_binance_funding_rates(_BINANCE_ALL)
    assert set(parsed.keys()) == {"BTC-USDT", "ETH-USDT"}
    assert parsed["BTC-USDT"]["fundingRate"] == pytest.approx(0.01)
    assert parsed["BTC-USDT"]["nextFundingTime"] == 1787600000000


def test_format_funding_rates_response_includes_source():
    """API 回應帶 source，前端才能顯示來源。"""
    from api.routers.market.helpers import format_funding_rates_response

    resp = format_funding_rates_response(
        timestamp="2026-08-25T00:00:00Z",
        total_count=2,
        top_bullish=[("BTC-USDT", 0.01)],
        top_bearish=[("ETH-USDT", -0.02)],
        source="binance",
    )
    assert resp["source"] == "binance"
    assert resp["top_bullish"][0] == {"symbol": "BTC-USDT", "fundingRate": 0.01}


# ── 3. 前端 i18n：來源標示 key（4 語系）─────────────────────────────────────


@pytest.mark.parametrize("lang", ["zh-TW", "zh-CN", "en", "ru"])
def test_frontend_i18n_funding_source_key(lang):
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "web" / "js" / "i18n" / f"{lang}.json"
    )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    key = data.get("market", {}).get("fundingSource")
    assert key and "{{source}}" in key, f"{lang} 缺少 market.fundingSource（含 {{source}} 插值）"
