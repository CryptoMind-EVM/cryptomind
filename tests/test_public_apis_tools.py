"""public-apis 清單挑進來的免金鑰來源（2026-09-13）：SEC EDGAR 申報工具、BTC 網路狀態工具、
匯率／幣價備援鏈（以前法幣對永遠用寫死的 31.5、加密貨幣只認 14 個幣）、四處註冊守衛。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tools import sec_edgar
from core.tools.crypto_modules import btc_network
from core.tools.crypto_modules import exchange_rate as fx

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or ("{}" if payload is None else "x")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("err", request=None, response=self)


# ── SEC EDGAR ────────────────────────────────────────────────────────────────

_SUBMISSIONS = {
    "cik": 1045810,
    "name": "NVIDIA CORP",
    "sicDescription": "Semiconductors",
    "fiscalYearEnd": "0126",
    "filings": {
        "recent": {
            "form": ["4", "8-K", "S-8", "10-Q", "10-K/A"],
            "filingDate": [
                "2026-09-11",
                "2026-09-03",
                "2026-08-30",
                "2026-08-27",
                "2026-03-01",
            ],
            "reportDate": ["2026-09-09", "2026-09-03", "", "2026-07-26", "2026-01-25"],
            "accessionNumber": [
                "0002152188-26-000005",
                "0001045810-26-000100",
                "0001045810-26-000090",
                "0001045810-26-000080",
                "0001045810-26-000010",
            ],
            "primaryDocument": [
                "xslF345X06/wk-form4.xml",
                "nvda-8k.htm",
                "s8.htm",
                "nvda-10q.htm",
                "nvda-10ka.htm",
            ],
            "primaryDocDescription": ["FORM 4", "8-K", "S-8", "10-Q", "10-K/A"],
            "items": ["", "2.02,9.01", "", "", ""],
        }
    },
}


class TestSecEdgar:
    def test_recent_filings_filters_and_links(self):
        rows = sec_edgar.recent_filings(_SUBMISSIONS, limit=10)
        assert [r["form"] for r in rows] == [
            "4",
            "8-K",
            "10-Q",
            "10-K/A",
        ]  # S-8 濾掉；10-K/A 保留
        eightk = rows[1]
        assert (
            eightk["label"] == "Current report – material event (8-K)" and eightk["items"] == "2.02,9.01"
        )
        assert (
            eightk["url"]
            == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000100/nvda-8k.htm"
        )
        assert sec_edgar.recent_filings(_SUBMISSIONS, limit=2)[1]["form"] == "8-K"
        assert len(sec_edgar.recent_filings(_SUBMISSIONS, all_forms=True)) == 5

    def test_ticker_map_parses_ticker_txt_and_normalises_dots(self, monkeypatch):
        monkeypatch.setattr(sec_edgar, "_tickers_cache", {"at": 0.0, "map": {}})
        monkeypatch.setattr(
            sec_edgar.httpx,
            "get",
            lambda *a, **k: _Resp(
                200, None, "nvda\t1045810\nbrk-b\t1067983\nbad line\n"
            ),
        )
        assert sec_edgar.ticker_to_cik("nvda") == 1045810
        assert sec_edgar.ticker_to_cik("BRK.B") == 1067983
        assert sec_edgar.ticker_to_cik("NOPE") is None

    def test_ticker_map_falls_back_to_json(self, monkeypatch):
        monkeypatch.setattr(sec_edgar, "_tickers_cache", {"at": 0.0, "map": {}})
        calls = []

        def fake_get(url, **k):
            calls.append(url)
            if url == sec_edgar.TICKERS_URL:
                raise RuntimeError("timeout")
            return _Resp(
                200, {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}}
            )

        monkeypatch.setattr(sec_edgar.httpx, "get", fake_get)
        assert sec_edgar.ticker_to_cik("AAPL") == 320193
        assert calls == [sec_edgar.TICKERS_URL, sec_edgar.TICKERS_JSON_URL]

    def test_tool_end_to_end_and_errors(self, monkeypatch):
        monkeypatch.setattr(
            sec_edgar,
            "ticker_to_cik",
            lambda s: 1045810 if s.upper() == "NVDA" else None,
        )
        monkeypatch.setattr(sec_edgar, "fetch_submissions", lambda cik: _SUBMISSIONS)
        out = sec_edgar.sec_filings.invoke({"symbol": "nvda", "limit": 3})
        assert (
            out["company"] == "NVIDIA CORP"
            and out["count"] == 3
            and out["symbol"] == "NVDA"
        )
        assert out["filings"][0]["url"].startswith("https://www.sec.gov/Archives/")
        assert "not found" in sec_edgar.sec_filings.invoke({"symbol": "ZZZZ"})["error"]
        assert "error" in sec_edgar.sec_filings.invoke(
            {"symbol": "BTC"}
        )  # 幣被導回幣工具
        monkeypatch.setattr(
            sec_edgar,
            "fetch_submissions",
            lambda cik: (_ for _ in ()).throw(RuntimeError("x")),
        )
        assert "unavailable" in sec_edgar.sec_filings.invoke({"symbol": "NVDA"})["error"]

    def test_user_agent_header_is_sent(self, monkeypatch):
        monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Test/1.0 (a@b.c)")
        assert sec_edgar._headers()["User-Agent"] == "Test/1.0 (a@b.c)"
        monkeypatch.delenv("SEC_EDGAR_USER_AGENT")
        assert "CryptoMind" in sec_edgar._headers()["User-Agent"]


# ── BTC 網路 ─────────────────────────────────────────────────────────────────


class TestBtcNetwork:
    def test_halving_math(self):
        h = btc_network.halving_info(966_692)
        assert h["next_halving_height"] == 1_050_000 and h["blocks_remaining"] == 83_308
        assert (
            h["current_block_reward_btc"] == 3.125
            and h["block_reward_after_btc"] == 1.5625
        )
        assert btc_network.halving_info(1_050_000)["next_halving_height"] == 1_260_000

    def test_congestion_label(self):
        assert btc_network.congestion_label(2_000_000) == "low"
        assert btc_network.congestion_label(20_000_000) == "moderate"
        assert btc_network.congestion_label(40_000_000) == "high"

    def test_tool_assembles_and_caches(self, monkeypatch):
        from core.tools.crypto_modules import common

        common._COINGECKO_CACHE.pop(btc_network.CACHE_KEY, None)
        payload = {
            "/v1/fees/recommended": {
                "fastestFee": 2,
                "halfHourFee": 2,
                "hourFee": 1,
                "economyFee": 1,
                "minimumFee": 1,
            },
            "/mempool": {"count": 79334, "vsize": 40_150_000},
            "/blocks/tip/height": "966692",
            "/v1/difficulty-adjustment": {
                "progressPercent": 50.99,
                "difficultyChange": 4.85,
                "remainingBlocks": 988,
            },
        }
        calls = []

        def fake_get(path):
            calls.append(path)
            return payload[path]

        monkeypatch.setattr(btc_network, "_get", fake_get)
        out = btc_network.get_btc_network_status.invoke({})
        assert (
            out["fees_sat_per_vb"]["fastest"] == 2
            and out["mempool"]["congestion"] == "high"
        )
        assert (
            out["block_height"] == 966692
            and out["halving"]["blocks_remaining"] == 83_308
        )
        assert out["difficulty_adjustment"]["estimated_change_percent"] == 4.85
        again = btc_network.get_btc_network_status.invoke({})
        assert again == out and len(calls) == 4, "60 秒內第二次要走快取"
        common._COINGECKO_CACHE.pop(btc_network.CACHE_KEY, None)
        monkeypatch.setattr(
            btc_network,
            "_get",
            lambda path: (_ for _ in ()).throw(RuntimeError("down")),
        )
        assert "error" in btc_network.get_btc_network_status.invoke({})


# ── 匯率／幣價備援鏈 ─────────────────────────────────────────────────────────


class TestRateChain:
    def setup_method(self):
        fx._rate_cache.clear()

    def test_fiat_pair_prefers_live_over_static(self, monkeypatch):
        monkeypatch.setattr(fx, "_forex_exchangerate_api", lambda a, b: 31.58)
        assert fx.get_exchange_rate("USD", "TWD") == 31.58  # 以前這裡永遠是靜態 31.5

    def test_fiat_pair_walks_the_chain_then_static(self, monkeypatch):
        order = []
        monkeypatch.setattr(
            fx, "_forex_exchangerate_api", lambda a, b: order.append("era") or None
        )
        monkeypatch.setattr(
            fx, "_forex_currency_api", lambda a, b: order.append("cur") or None
        )
        monkeypatch.setattr(
            fx, "_forex_frankfurter", lambda a, b: order.append("frank") or None
        )
        assert fx.get_exchange_rate("USD", "TWD") == pytest.approx(31.5)  # 靜態表墊底
        assert order == ["era", "cur", "frank"]
        fx._rate_cache.clear()
        monkeypatch.setattr(fx, "_forex_currency_api", lambda a, b: 1340.9)
        assert (
            fx.get_exchange_rate("USD", "KRW") == 1340.9
        )  # 靜態表沒有 KRW，靠 currency-api

    def test_frankfurter_skips_unsupported_currency(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            fx.httpx if hasattr(fx, "httpx") else __import__("httpx"),
            "get",
            lambda *a, **k: called.append(a) or _Resp(200, {"rates": {}}),
        )
        assert fx._forex_frankfurter("USD", "TWD") is None and called == []

    def test_currency_api_host_fallback(self, monkeypatch):
        import httpx

        def fake_get(url, **k):
            if "jsdelivr" in url:
                raise RuntimeError("cdn down")
            return _Resp(200, {"usd": {"twd": 31.58, "jpy": 153.6}})

        monkeypatch.setattr(httpx, "get", fake_get)
        assert fx._forex_currency_api("USD", "TWD") == 31.58
        assert fx._forex_currency_api("USD", "XXX") is None

    def test_crypto_walks_sources_and_any_listed_coin_works(self, monkeypatch):
        order = []
        monkeypatch.setattr(
            fx, "_crypto_usd_coingecko", lambda s: order.append("cg") or None
        )
        monkeypatch.setattr(
            fx, "_crypto_usd_okx", lambda s: order.append("okx") or None
        )
        monkeypatch.setattr(
            fx, "_crypto_usd_binance", lambda s: order.append("bn") or 0.57
        )
        monkeypatch.setattr(
            fx, "_crypto_usd_coinpaprika", lambda s: order.append("cp") or 9.9
        )
        assert fx.get_exchange_rate("AERO", "USD") == 0.57  # 以前不在 14 幣清單 → None
        assert order == ["cg", "okx", "bn"]
        fx._rate_cache.clear()
        monkeypatch.setattr(fx, "_forex_exchangerate_api", lambda a, b: 31.0)
        assert fx.get_exchange_rate("AERO", "TWD") == pytest.approx(0.57 * 31.0)

    def test_unknown_symbol_returns_none_not_static(self, monkeypatch):
        for name in (
            "_crypto_usd_coingecko",
            "_crypto_usd_okx",
            "_crypto_usd_binance",
            "_crypto_usd_coinpaprika",
        ):
            monkeypatch.setattr(fx, name, lambda s: None)
        for name in (
            "_forex_exchangerate_api",
            "_forex_currency_api",
            "_forex_frankfurter",
        ):
            monkeypatch.setattr(fx, name, lambda a, b: None)
        assert fx.get_exchange_rate("XYZNOPE", "USD") is None

    def test_coingecko_does_not_guess_ids(self, monkeypatch):
        import httpx

        monkeypatch.setattr(
            httpx,
            "get",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call")),
        )
        assert fx._crypto_usd_coingecko("AERO2") is None

    def test_coinpaprika_search_path_has_trailing_slash_and_exact_symbol(
        self, monkeypatch
    ):
        import httpx

        seen = []

        def fake_get(url, **k):
            seen.append(url)
            if url.endswith("/search/"):
                return _Resp(
                    200,
                    {
                        "currencies": [
                            {"id": "aero-fake", "symbol": "AEROX", "rank": 1},
                            {
                                "id": "aero-aerodrome-finance",
                                "symbol": "AERO",
                                "rank": 200,
                            },
                        ]
                    },
                )
            return _Resp(200, {"quotes": {"USD": {"price": 0.571}}})

        monkeypatch.setattr(httpx, "get", fake_get)
        assert fx._crypto_usd_coinpaprika("AERO") == 0.571
        assert seen[0] == "https://api.coinpaprika.com/v1/search/" and seen[1].endswith(
            "/tickers/aero-aerodrome-finance"
        )


# ── 註冊守衛 ─────────────────────────────────────────────────────────────────


def test_tools_registered_seeded_translated_and_exported():
    bootstrap = (REPO / "core" / "agents" / "bootstrap.py").read_text(encoding="utf-8")
    seed = (REPO / "core" / "database" / "tools.py").read_text(encoding="utf-8")
    trans = (REPO / "core" / "agents" / "tool_name_translations.py").read_text(
        encoding="utf-8"
    )
    for name in ("sec_filings", "get_btc_network_status"):
        assert f'name="{name}"' in bootstrap, f"bootstrap 沒註冊 {name}"
        assert f'"tool_id": "{name}"' in seed, f"seed 沒有 {name}"
        assert f'"{name}": {{' in trans, f"tool_name_translations 缺 {name}"
    for category, name in (
        ("us_stock", "sec_filings"),
        ("cryptomind", "sec_filings"),
        ("crypto", "get_btc_network_status"),
        ("cryptomind", "get_btc_network_status"),
    ):
        block = seed[seed.index(f'"{category}": [') :]
        block = block[: block.index("]")]
        assert name in block, f"{category} 清單缺 {name}"
    from core.tools.crypto_modules import get_btc_network_status as _g  # noqa: F401
    from core.tools.crypto_tools import get_btc_network_status  # noqa: F401

    env = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "SEC_EDGAR_USER_AGENT" in env
