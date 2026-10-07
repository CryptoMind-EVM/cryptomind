"""Stock data cache 行為測試（2026-08 業界 TTL 統一）。

驗證「補起來」的快取真的生效——第二次呼叫不打外部（yfinance/TWSE/Google News），
用 monkeypatch 避免實際網路呼叫。
"""


async def test_twse_institutional_detail_cached_on_second_call(monkeypatch):
    """get_twse_institutional_detail 補的 6h 快取：第二次不重掃 _fetch_institutional。"""
    from core.providers.twse_provider import TWSEProvider

    calls = {"n": 0}

    def fake_fetch(self, code):
        calls["n"] += 1
        return {
            "date": "20260812",
            "foreign_net": 100,
            "investment_trust": 50,
            "dealer_net": -20,
            "total_3party_net": 130,
        }

    monkeypatch.setattr(TWSEProvider, "_fetch_institutional", fake_fetch)
    p = TWSEProvider()
    p.clear_cache()
    r1 = p.get_twse_institutional_detail("2330")
    r2 = p.get_twse_institutional_detail("2330")
    assert r1 == r2
    assert r1["foreign_net"] == 100
    assert calls["n"] == 1  # 快取命中 → 第二次不重抓


async def test_fetch_news_sync_delegates_to_provider(monkeypatch):
    """yf_helpers.fetch_news_sync 走 provider.get_news（享 300s 快取），不再自己抓 yf。"""
    from api.routers import yf_helpers as mod

    seen = {"symbols": []}

    class FakeProvider:
        def get_news(self, symbol, limit):
            seen["symbols"].append(symbol)
            return [{"symbol": symbol, "title": "fake news"}]

    monkeypatch.setattr(mod, "_shared_yf_provider", FakeProvider)
    r = mod.fetch_news_sync("2330.TW", 5)
    assert r == [{"symbol": "2330.TW", "title": "fake news"}]
    assert seen["symbols"] == ["2330.TW"]  # 確實委派給 provider


def test_tw_foreign_holding_top20_cached(monkeypatch):
    """tw_foreign_holding_top20 tool 的 6h 快取：第二次不打 TWSE。"""
    import httpx

    import core.tools.tw_stock_tools as tools

    tools._FOREIGN_HOLDING_CACHE.clear()
    calls = {"n": 0}

    class _FakeResp:
        status_code = 200

        def json(self):
            calls["n"] += 1
            return [
                {
                    "Rank": 1,
                    "Code": "2330",
                    "Name": "TSMC",
                    "SharesHeldPer": 80.0,
                    "AvailableInvestPer": 20.0,
                    "Upperlimit": 100.0,
                }
            ]

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp())
    r1 = tools.tw_foreign_holding_top20.invoke({})
    r2 = tools.tw_foreign_holding_top20.invoke({})
    assert r1 == r2
    assert r1[0]["code"] == "2330"
    assert calls["n"] == 1  # 6h 快取命中 → 第二次不打 TWSE
