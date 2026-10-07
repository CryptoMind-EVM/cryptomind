"""使用者自選清單（2026-09-27；c056、api/routers/watchlist.py、早報與行事曆）。

全部 mock：不打 DB、不抓真的報價。前端在 tests/js/watchlist_settings.mjs。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import trading

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
USER = {"user_id": "u_watch", "username": "w", "membership_tier": "free"}


class TestNormalize:
    @pytest.mark.parametrize(
        "market,raw,out",
        [
            ("crypto", "btc", "BTC"),
            ("crypto", "BTC-USDT", "BTC"),
            ("crypto", "eth/usdt", "ETH"),
            ("crypto", "SOLUSDT", "SOL"),
            ("crypto", "USDC", "USDC"),
            ("tw_stock", "2330", "2330"),
            ("tw_stock", "2330.tw", "2330"),
            ("tw_stock", "00878", "00878"),
            ("tw_stock", "6488.TWO", "6488"),
            ("us_stock", " aapl ", "AAPL"),
            ("us_stock", "brk.b", "BRK.B"),
            ("us_stock", "BRK-B", "BRK-B"),
            ("hk_stock", "700", "0700.HK"),
            ("hk_stock", "0700.hk", "0700.HK"),
            ("hk_stock", "09988", "9988.HK"),
            ("jp_stock", "7203", "7203.T"),
            ("jp_stock", "7203.T", "7203.T"),
            ("kr_stock", "005930", "005930.KS"),
            ("kr_stock", "035720.KQ", "035720.KQ"),
            ("cn_stock", "600519", "600519.SS"),
            ("cn_stock", "000858", "000858.SZ"),
            ("cn_stock", "300750.sz", "300750.SZ"),
            ("in_stock", "reliance", "RELIANCE.NS"),
            ("in_stock", "TCS.BO", "TCS.BO"),
            ("commodity", "gc", "GC=F"),
            ("commodity", "CL=F", "CL=F"),
            ("forex", "EUR/USD", "EURUSD=X"),
            ("forex", "usdjpy", "USDJPY=X"),
            ("forex", "TWD=X", "TWD=X"),
        ],
    )
    def test_valid(self, market, raw, out):
        assert trading.normalize_symbol(market, raw) == out

    @pytest.mark.parametrize(
        "market,raw",
        [
            ("crypto", ""),
            ("crypto", "B"),
            ("crypto", "<script>"),
            ("tw_stock", "AAPL"),
            ("tw_stock", "23"),
            ("us_stock", "2330"),
            ("us_stock", "TOOLONGTICKER"),
            ("hk_stock", "TENCENT"),
            ("hk_stock", "123456"),
            ("jp_stock", "72"),
            ("kr_stock", "5930"),
            ("cn_stock", "60051"),
            ("in_stock", "REL IANCE!"),
            ("commodity", "GOLDEN"),
            ("forex", "EURO"),
            ("mars_stock", "X"),
        ],
    )
    def test_invalid(self, market, raw):
        assert trading.normalize_symbol(market, raw) is None


class TestStore:
    def test_get_returns_market_and_symbol(self):
        rows = [{"market": "crypto", "symbol": "BTC"}, {"market": "tw_stock", "symbol": "2330"}]
        with patch("core.database.trading.DatabaseBase.query_all", return_value=rows) as q:
            assert trading.get_watchlist("u1") == rows
        assert "FROM user_watchlist" in q.call_args.args[0]

    def test_add_is_idempotent(self):
        with patch("core.database.trading.DatabaseBase.execute", return_value=0) as ex:
            assert trading.add_to_watchlist("u1", "crypto", "BTC") is False
        assert "ON CONFLICT DO NOTHING" in ex.call_args.args[0]
        assert ex.call_args.args[1] == ("u1", "crypto", "BTC")


def _client():
    from slowapi.errors import RateLimitExceeded

    from api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
    from api.routers import watchlist as mod

    limiter.reset()
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(mod.router)

    async def fake_user():
        return USER

    app.dependency_overrides[mod.get_current_user] = fake_user
    return TestClient(app)


class TestApi:
    def test_get(self):
        items = [{"market": "crypto", "symbol": "BTC"}]
        with patch.object(trading, "get_watchlist", return_value=items):
            res = _client().get("/api/watchlist")
        assert res.status_code == 200
        body = res.json()
        assert body["items"] == items
        assert body["max"] == 50 and body["markets"][:3] == ["crypto", "tw_stock", "us_stock"]

    def test_add_normalizes_checks_price_and_saves(self):
        with (
            patch.object(trading, "get_watchlist", return_value=[]),
            patch("api.alert_checker._fetch_price", new=AsyncMock(return_value=(65000.0, 64000.0))) as price,
            patch.object(trading, "add_to_watchlist", return_value=True) as add,
        ):
            res = _client().post("/api/watchlist/add", json={"market": "crypto", "symbol": "btc-usdt"})
        assert res.status_code == 200
        assert res.json()["item"] == {"market": "crypto", "symbol": "BTC"}
        price.assert_awaited_once_with("BTC", "crypto")
        add.assert_called_once_with("u_watch", "crypto", "BTC")

    def test_unknown_symbol_is_404_and_not_saved(self):
        with (
            patch.object(trading, "get_watchlist", return_value=[]),
            patch("api.alert_checker._fetch_price", new=AsyncMock(return_value=None)),
            patch.object(trading, "add_to_watchlist") as add,
        ):
            res = _client().post("/api/watchlist/add", json={"market": "us_stock", "symbol": "ZZZZZ"})
        assert res.status_code == 404
        add.assert_not_called()

    def test_bad_format_is_422_without_price_lookup(self):
        with patch("api.alert_checker._fetch_price", new=AsyncMock()) as price:
            res = _client().post("/api/watchlist/add", json={"market": "tw_stock", "symbol": "AAPL"})
        assert res.status_code == 422
        price.assert_not_awaited()

    def test_unsupported_market_is_422(self):
        res = _client().post("/api/watchlist/add", json={"market": "mars_stock", "symbol": "0700"})
        assert res.status_code == 422

    def test_market_lists_agree(self):
        """自選 API、自選 store、價格警報（API＋store）、早報報價：同一組 10 個市場。"""
        from typing import get_args

        from api.models import CreateAlertRequest
        from api.routers.watchlist import Market
        from core.daily_brief.collect import PRICEABLE_MARKETS
        from core.database.price_alerts import VALID_MARKETS

        markets = set(trading.WATCHLIST_MARKETS)
        assert len(markets) == 10
        assert set(get_args(Market)) == markets
        assert set(get_args(CreateAlertRequest.model_fields["market"].annotation)) == markets
        assert set(VALID_MARKETS) == markets
        assert set(PRICEABLE_MARKETS) == markets

    def test_full_is_400(self):
        items = [{"market": "crypto", "symbol": f"C{i}"} for i in range(trading.MAX_WATCHLIST)]
        with (
            patch.object(trading, "get_watchlist", return_value=items),
            patch("api.alert_checker._fetch_price", new=AsyncMock()) as price,
        ):
            res = _client().post("/api/watchlist/add", json={"market": "crypto", "symbol": "BTC"})
        assert res.status_code == 400
        price.assert_not_awaited()

    def test_duplicate_is_ok_without_price_lookup(self):
        with (
            patch.object(trading, "get_watchlist", return_value=[{"market": "crypto", "symbol": "BTC"}]),
            patch("api.alert_checker._fetch_price", new=AsyncMock()) as price,
            patch.object(trading, "add_to_watchlist") as add,
        ):
            res = _client().post("/api/watchlist/add", json={"market": "crypto", "symbol": "btc"})
        assert res.status_code == 200 and res.json()["added"] is False
        price.assert_not_awaited()
        add.assert_not_called()

    def test_remove(self):
        with patch.object(trading, "remove_from_watchlist", return_value=True) as rm:
            res = _client().post("/api/watchlist/remove", json={"market": "tw_stock", "symbol": "2330.TW"})
        assert res.status_code == 200 and res.json()["removed"] is True
        rm.assert_called_once_with("u_watch", "tw_stock", "2330")


class TestBriefCollect:
    async def test_watchlist_prices_by_market_skip_held_keep_missing(self):
        from core.daily_brief import collect
        from core.daily_brief.schedule import BriefPrefs

        class _Repo:
            base_currency = "TWD"

            def get_positions(self):
                return [{"symbol": "BTC", "market": "crypto", "quantity": 1}]

        prices = {("BTC", "crypto"): (65000.0, 64000.0), ("2330", "tw_stock"): (1000.0, 980.0)}
        seen = []

        async def fake_price(symbol, market):
            seen.append((symbol, market))
            return prices.get((symbol, market))

        watch = [
            {"market": "crypto", "symbol": "BTC"},  # 已在持倉
            {"market": "tw_stock", "symbol": "2330"},
            {"market": "us_stock", "symbol": "AAPL"},  # 報價抓不到也要列
        ]
        prefs = BriefPrefs(
            user_id="u1",
            enabled=True,
            send_hour=8,
            timezone="Asia/Taipei",
            include_spend=False,
            include_macro=False,
        )
        with (
            patch("core.orm.trade_journal_repo.get_journal_repo", return_value=_Repo()),
            patch.object(collect, "_fetch_price_safe", side_effect=fake_price),
            patch("core.database.trading.get_watchlist", return_value=watch),
            patch("core.daily_brief.store.recent_alert_notifications", return_value=[]),
            patch("core.database.price_alerts.get_user_alerts", return_value=[]),
        ):
            data = await collect.collect_brief_data(prefs, datetime(2026, 9, 27, 0, tzinfo=timezone.utc))
        assert [(w["market"], w["symbol"]) for w in data.watchlist] == [
            ("tw_stock", "2330"),
            ("us_stock", "AAPL"),
        ]
        assert data.watchlist[0]["price"] == 1000.0 and round(data.watchlist[0]["change_pct"], 2) == 2.04
        assert data.watchlist[1]["price"] is None, "抓不到報價也列，排最後"
        assert ("2330", "tw_stock") in seen and ("AAPL", "us_stock") in seen

    async def test_watchlist_sorted_by_biggest_movers(self):
        from core.daily_brief import collect
        from core.daily_brief.schedule import BriefPrefs

        class _Repo:
            base_currency = "TWD"

            def get_positions(self):
                return []

        quotes = {"A": (101.0, 100.0), "B": (90.0, 100.0), "C": None, "D": (103.0, 100.0)}

        async def fake_price(symbol, market):
            return quotes[symbol]

        watch = [{"market": "hk_stock", "symbol": s} for s in "ABCD"]
        prefs = BriefPrefs(
            user_id="u1", enabled=True, send_hour=8, timezone="Asia/Taipei",
            include_spend=False, include_macro=False,
        )
        with (
            patch("core.orm.trade_journal_repo.get_journal_repo", return_value=_Repo()),
            patch.object(collect, "_fetch_price_safe", side_effect=fake_price),
            patch("core.database.trading.get_watchlist", return_value=watch),
            patch("core.daily_brief.store.recent_alert_notifications", return_value=[]),
        ):
            data = await collect.collect_brief_data(prefs, datetime(2026, 9, 27, 0, tzinfo=timezone.utc))
        assert [w["symbol"] for w in data.watchlist] == ["B", "D", "A", "C"]


class TestCalendarSync:
    def test_items_with_market_pass_through(self):
        from core.daily_brief.calendar_sync import watchlist_positions

        rows = watchlist_positions(
            [{"market": "us_stock", "symbol": "SOL"}, {"market": "crypto", "symbol": "SOL"}, "2330"]
        )
        assert [(r["market"], r["symbol"]) for r in rows] == [
            ("us_stock", "SOL"),
            ("crypto", "SOL"),
            ("tw_stock", "2330"),
        ]
        assert all(r["watch"] and r["quantity"] == 0 for r in rows)


class TestSchema:
    MIG = REPO / "alembic" / "versions" / "c056_user_watchlist.py"

    @staticmethod
    def _ddl(src: str) -> str:
        m = re.search(
            r"CREATE TABLE IF NOT EXISTS user_watchlist \((.*?)\n\s*\)\s*\"\"\"", src, re.S
        )
        assert m
        return re.sub(r"\s+", " ", m.group(1)).strip()

    def test_migration_chain_and_old_table_dropped(self):
        src = self.MIG.read_text(encoding="utf-8")
        assert 'revision = "c056"' in src and 'down_revision = "c055"' in src
        assert "DROP TABLE watchlist" in src and "DROP TABLE IF EXISTS user_watchlist" in src
        downs = [
            m.group(1)
            for f in (REPO / "alembic" / "versions").glob("c05*.py")
            if (m := re.search(r'^down_revision = "(\w+)"', f.read_text(encoding="utf-8"), re.M))
        ]
        assert downs.count("c055") == 1

    def test_schema_matches_and_old_table_not_recreated(self):
        schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
        assert '("user_watchlist", create_user_watchlist_table)' in schema
        assert self._ddl(schema) == self._ddl(self.MIG.read_text(encoding="utf-8"))
        # 啟動時的 schema reconcile 不能把舊表建回來
        assert "CREATE TABLE IF NOT EXISTS watchlist" not in schema


class TestFrontendWiring:
    def test_wiring(self):
        spa = (REPO / "web/js/spa.js").read_text(encoding="utf-8")
        settings_loader = spa[spa.index("    settings: () =>") :]
        assert "import('./alerts.js')" in settings_loader.split("]).then")[0]
        assert "loadWatchlist()" in (REPO / "web/js/brief-settings.js").read_text(encoding="utf-8")
        assert 'id="brief-watchlist-section"' in (
            REPO / "web/js/components/tab-settings.js"
        ).read_text(encoding="utf-8")
        assert "'WatchlistSettings'" in (REPO / "web/js/click-delegator.js").read_text(encoding="utf-8")
        assert "alerts:changed" in (REPO / "web/js/alerts.js").read_text(encoding="utf-8")

    @pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
    def test_node_behaviour(self):
        out = subprocess.run(
            ["node", "tests/js/watchlist_settings.mjs"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]


class TestReplaceMarket:
    """PUT /api/watchlist/{market}：各市場分頁整批同步（web/js/watchlist-sync.js）。"""

    def test_normalizes_dedupes_and_drops_invalid(self):
        with (
            patch.object(trading, "get_watchlist", return_value=[]),
            patch.object(trading, "replace_market", side_effect=lambda u, m, s: s) as rep,
        ):
            res = _client().put(
                "/api/watchlist/hk_stock",
                json={"symbols": ["0700.HK", "700", "9988.HK", "TENCENT", "3690.hk"]},
            )
        assert res.status_code == 200
        body = res.json()
        assert body["symbols"] == ["0700.HK", "9988.HK", "3690.HK"] and body["truncated"] is False
        rep.assert_called_once_with("u_watch", "hk_stock", ["0700.HK", "9988.HK", "3690.HK"])

    def test_crypto_pairs_become_coins(self):
        with (
            patch.object(trading, "get_watchlist", return_value=[]),
            patch.object(trading, "replace_market", side_effect=lambda u, m, s: s),
        ):
            res = _client().put("/api/watchlist/crypto", json={"symbols": ["BTC-USDT", "ETH-USDT"]})
        assert res.json()["symbols"] == ["BTC", "ETH"]

    def test_truncates_to_room_left_by_other_markets(self):
        others = [{"market": "us_stock", "symbol": f"S{i}"} for i in range(trading.MAX_WATCHLIST - 2)]
        with (
            patch.object(trading, "get_watchlist", return_value=others),
            patch.object(trading, "replace_market", side_effect=lambda u, m, s: s) as rep,
        ):
            res = _client().put("/api/watchlist/jp_stock", json={"symbols": ["7203", "6758", "9984"]})
        assert res.json() == {
            "success": True,
            "market": "jp_stock",
            "symbols": ["7203.T", "6758.T"],
            "truncated": True,
        }
        assert rep.call_args.args[2] == ["7203.T", "6758.T"]

    def test_empty_list_clears_market(self):
        with (
            patch.object(trading, "get_watchlist", return_value=[]),
            patch.object(trading, "replace_market", side_effect=lambda u, m, s: s) as rep,
        ):
            res = _client().put("/api/watchlist/crypto", json={"symbols": []})
        assert res.status_code == 200
        rep.assert_called_once_with("u_watch", "crypto", [])

    def test_unknown_market_is_422(self):
        res = _client().put("/api/watchlist/mars_stock", json={"symbols": ["X"]})
        assert res.status_code == 422

    def test_replace_market_sql(self):
        from contextlib import contextmanager

        log = []

        class _Cur:
            def execute(self, sql, params=None):
                log.append((" ".join(sql.split()), params))

            def fetchall(self):
                return [("0700.HK",), ("0005.HK",)]

        class _Conn:
            def cursor(self):
                return _Cur()

        @contextmanager
        def tx():
            yield _Conn()

        with patch("core.database.base.transaction", tx):
            trading.replace_market("u1", "hk_stock", ["0700.HK", "9988.HK"])
        deletes = [p for s, p in log if s.startswith("DELETE")]
        inserts = [p for s, p in log if s.startswith("INSERT")]
        assert deletes == [("u1", "hk_stock", "0005.HK")], "不在新清單的刪掉"
        assert inserts == [("u1", "hk_stock", "9988.HK", 1)], "已存在的保留，新的照順序加"


class TestTabSyncWiring:
    TABS = {
        "twstock.js": "tw_stock",
        "usstock.js": "us_stock",
        "hkstock.js": "hk_stock",
        "jpstock.js": "jp_stock",
        "krstock.js": "kr_stock",
        "astock.js": "cn_stock",
        "instock.js": "in_stock",
        "commodity.js": "commodity",
        "forex.js": "forex",
    }

    @pytest.mark.parametrize("name,market", list(TABS.items()))
    def test_each_tab_hydrates_pushes_and_has_bell(self, name, market):
        src = (REPO / "web" / "js" / name).read_text(encoding="utf-8")
        assert "WatchlistSync.push(" in src, f"{name} 改清單要寫回伺服器"
        assert "ydrateServerWatchlist();" in src, f"{name} init 要問伺服器"
        assert market in src
        assert 'data-click="openAlert"' in src and f"'{market}'" in src, f"{name} 卡片要有 🔔"

    def test_crypto_tab_syncs(self):
        filt = (REPO / "web/js/filter.js").read_text(encoding="utf-8")
        screener = (REPO / "web/js/market-screener.js").read_text(encoding="utf-8")
        assert "WatchlistSync.push('crypto'" in filt
        assert "WatchlistSync.hydrate('crypto'" in screener

    def test_loaders_and_entry(self):
        spa = (REPO / "web/js/spa.js").read_text(encoding="utf-8")
        for tab in ("commodity", "forex", "hkstock", "astock", "jpstock", "instock", "krstock", "twstock", "usstock"):
            block = spa[spa.index(f"    {tab}: () =>") :].split("]),", 1)[0]
            assert "import('./alerts.js')" in block, f"{tab} 分頁要載 alerts.js（🔔）"
        assert "import './watchlist-sync.js'" in (REPO / "web/js/main.js").read_text(encoding="utf-8")

    @pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
    def test_node_sync_behaviour(self):
        out = subprocess.run(
            ["node", "tests/js/watchlist_sync.mjs"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]
