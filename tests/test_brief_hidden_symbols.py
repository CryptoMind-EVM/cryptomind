"""早報不列的標的（2026-09-28；c057、core/daily_brief/store.py、api/routers/brief.py）。

使用者可以把某一檔（自選或投資日誌的持倉）從早報拿掉，自選清單與帳本都不動。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
USER = {"user_id": "u_hide", "username": "hide", "membership_tier": "free"}


def _client():
    from api.routers import brief as brief_module

    app = FastAPI()
    app.include_router(brief_module.router)

    async def fake_user():
        return USER

    app.dependency_overrides[brief_module.get_current_user] = fake_user
    return TestClient(app)


class _Repo:
    base_currency = "TWD"

    def __init__(self, positions):
        self._positions = positions

    def get_positions(self):
        return self._positions


class TestCollectSkipsHidden:
    async def test_hidden_position_and_watchlist_items_are_left_out(self):
        from core.daily_brief import collect
        from core.daily_brief.schedule import BriefPrefs

        positions = [
            {"symbol": "BTC", "market": "crypto", "quantity": 1},
            {"symbol": "2330", "market": "tw_stock", "quantity": 10},
        ]
        watch = [
            {"market": "us_stock", "symbol": "NVDA"},
            {"market": "us_stock", "symbol": "AAPL"},
        ]
        seen = []

        async def fake_price(symbol, market):
            seen.append(symbol)
            return (101.0, 100.0)

        prefs = BriefPrefs(
            user_id="u1",
            enabled=True,
            send_hour=8,
            timezone="Asia/Taipei",
            include_spend=False,
            include_macro=False,
        )
        with (
            patch(
                "core.orm.trade_journal_repo.get_journal_repo",
                return_value=_Repo(positions),
            ),
            patch.object(collect, "_fetch_price_safe", side_effect=fake_price),
            patch("core.database.trading.get_watchlist", return_value=watch),
            patch(
                "core.daily_brief.store.hidden_symbols",
                return_value={("crypto", "BTC"), ("us_stock", "NVDA")},
            ),
            patch("core.daily_brief.store.recent_alert_notifications", return_value=[]),
            patch("core.database.price_alerts.get_user_alerts", return_value=[]),
        ):
            data = await collect.collect_brief_data(
                prefs, datetime(2026, 9, 28, 0, tzinfo=timezone.utc)
            )
        assert [p["symbol"] for p in data.positions] == ["2330"]
        assert [w["symbol"] for w in data.watchlist] == ["AAPL"]
        assert "BTC" not in seen and "NVDA" not in seen, "不列的標的連報價都不用抓"
        assert "BTC" not in data.alert_suggestions

    async def test_hidden_lookup_failure_does_not_block_the_brief(self):
        from core.daily_brief import collect
        from core.daily_brief.schedule import BriefPrefs

        async def fake_price(symbol, market):
            return (101.0, 100.0)

        prefs = BriefPrefs(
            user_id="u1",
            enabled=True,
            send_hour=8,
            timezone="Asia/Taipei",
            include_spend=False,
            include_macro=False,
        )
        with (
            patch(
                "core.orm.trade_journal_repo.get_journal_repo",
                return_value=_Repo(
                    [{"symbol": "ETH", "market": "crypto", "quantity": 2}]
                ),
            ),
            patch.object(collect, "_fetch_price_safe", side_effect=fake_price),
            patch("core.database.trading.get_watchlist", return_value=[]),
            patch(
                "core.daily_brief.store.hidden_symbols",
                side_effect=RuntimeError("no table"),
            ),
            patch("core.daily_brief.store.recent_alert_notifications", return_value=[]),
            patch("core.database.price_alerts.get_user_alerts", return_value=[]),
        ):
            data = await collect.collect_brief_data(
                prefs, datetime(2026, 9, 28, 0, tzinfo=timezone.utc)
            )
        assert [p["symbol"] for p in data.positions] == ["ETH"]


class TestStore:
    def test_hide_uppercases_and_respects_the_cap(self):
        from core.daily_brief import store

        with (
            patch.object(store.DatabaseBase, "query_one", return_value={"n": 0}),
            patch.object(store.DatabaseBase, "execute") as ex,
        ):
            assert store.set_symbol_hidden("u1", "us_stock", "nvda", True) is True
        assert ex.call_args[0][1] == ("u1", "us_stock", "NVDA")
        with (
            patch.object(
                store.DatabaseBase,
                "query_one",
                return_value={"n": store.MAX_HIDDEN_SYMBOLS},
            ),
            patch.object(store.DatabaseBase, "execute") as ex,
        ):
            assert store.set_symbol_hidden("u1", "us_stock", "TSLA", True) is False
        ex.assert_not_called()

    def test_show_deletes_without_counting(self):
        from core.daily_brief import store

        with (
            patch.object(store.DatabaseBase, "query_one") as q,
            patch.object(store.DatabaseBase, "execute") as ex,
        ):
            assert store.set_symbol_hidden("u1", "crypto", "btc", False) is True
        q.assert_not_called()
        assert "DELETE FROM user_brief_hidden_symbols" in ex.call_args[0][0]
        assert ex.call_args[0][1] == ("u1", "crypto", "BTC")


class TestApi:
    def test_get_lists_positions_once_and_hidden(self):
        positions = [
            {"symbol": "btc", "market": "crypto", "quantity": 1},
            {"symbol": "BTC", "market": "crypto", "quantity": 2},  # 同代號另一種持倉
            {"symbol": "0050", "market": "tw_stock", "quantity": 0},  # 已出清
        ]
        with (
            patch(
                "core.orm.trade_journal_repo.get_journal_repo",
                return_value=_Repo(positions),
            ),
            patch(
                "core.daily_brief.store.hidden_symbols",
                return_value={("us_stock", "NVDA")},
            ),
        ):
            res = _client().get("/api/user/brief-prefs/symbols")
        assert res.status_code == 200
        body = res.json()
        assert body["positions"] == [{"market": "crypto", "symbol": "BTC"}]
        assert body["hidden"] == [{"market": "us_stock", "symbol": "NVDA"}]

    def test_put_hides_and_shows(self):
        with patch(
            "core.daily_brief.store.set_symbol_hidden", return_value=True
        ) as setter:
            res = _client().put(
                "/api/user/brief-prefs/symbols",
                json={"market": "us_stock", "symbol": "nvda", "hidden": True},
            )
        assert res.status_code == 200 and res.json()["symbol"] == "NVDA"
        setter.assert_called_once_with("u_hide", "us_stock", "nvda", True)

    def test_put_over_cap_is_400(self):
        with patch("core.daily_brief.store.set_symbol_hidden", return_value=False):
            res = _client().put(
                "/api/user/brief-prefs/symbols",
                json={"market": "crypto", "symbol": "BTC", "hidden": True},
            )
        assert res.status_code == 400

    @pytest.mark.parametrize(
        "payload",
        [
            {"market": "Crypto!", "symbol": "BTC", "hidden": True},
            {"market": "crypto", "symbol": "<script>", "hidden": True},
            {"market": "crypto", "symbol": "X" * 25, "hidden": True},
            {"market": "crypto", "symbol": "BTC"},
        ],
    )
    def test_put_rejects_bad_input(self, payload):
        with patch("core.daily_brief.store.set_symbol_hidden") as setter:
            res = _client().put("/api/user/brief-prefs/symbols", json=payload)
        assert res.status_code == 422
        setter.assert_not_called()


class TestSchema:
    MIG = REPO / "alembic" / "versions" / "c057_user_brief_hidden_symbols.py"

    @staticmethod
    def _ddl(src: str) -> str:
        m = re.search(
            r"CREATE TABLE IF NOT EXISTS user_brief_hidden_symbols \((.*?)\n\s*\)\s*\"\"\"",
            src,
            re.S,
        )
        assert m
        return re.sub(r"\s+", " ", m.group(1)).strip()

    def test_migration_chain(self):
        src = self.MIG.read_text(encoding="utf-8")
        assert 'revision = "c057"' in src and 'down_revision = "c056"' in src
        assert "DROP TABLE IF EXISTS user_brief_hidden_symbols" in src
        downs = [
            m.group(1)
            for f in (REPO / "alembic" / "versions").glob("c05*.py")
            if (
                m := re.search(
                    r'^down_revision = "(\w+)"', f.read_text(encoding="utf-8"), re.M
                )
            )
        ]
        assert downs.count("c056") == 1

    def test_schema_matches_migration(self):
        schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
        assert (
            '("user_brief_hidden_symbols", create_user_brief_hidden_symbols_table)'
            in schema
        )
        assert self._ddl(schema) == self._ddl(self.MIG.read_text(encoding="utf-8"))
        assert "ON DELETE CASCADE" in self._ddl(schema), "刪帳號要跟著刪"


def test_settings_ui_wiring():
    settings = (REPO / "web" / "js" / "components" / "tab-settings.js").read_text(
        encoding="utf-8"
    )
    assert 'id="brief-positions-block"' in settings
    assert 'id="brief-position-items"' in settings
    js = (REPO / "web" / "js" / "watchlist-settings.js").read_text(encoding="utf-8")
    assert "/api/user/brief-prefs/symbols" in js
    assert "toggleBrief: (market, symbol) => toggleBriefSymbol(market, symbol)" in js
    assert 'data-click="WatchlistSettings.toggleBrief"' in js
    assert "aria-pressed" in js, "開關狀態不能只靠顏色"
